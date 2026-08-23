"""
Tests for the WhatsApp sequence stepper (audit P0-4).

The adapter is mocked in every single test. Nothing in here may open a socket
to Laravel, and nothing in here may send a WhatsApp message.

Coverage:
  * kill switch off => nothing is claimed and nothing is sent
  * a due step sends, advances current_step and schedules the next one
  * the last step completes the enrollment
  * REPLIED / OPTED_OUT / PAUSED enrollments are never stepped, including when
    the reply lands between the claim and the send
  * failures retry, then cap out into PAUSED with a last_error
  * idempotency: a SENT marker never sends twice; a SENDING marker left by a
    dead worker is never re-sent
  * select_for_update(skip_locked=True) stops two concurrent workers sending
    the same step twice
  * the 24h window: template sends are recorded with the canonical
    reply_window, and a step with no template outside the window fails with a
    clear error instead of being dropped
"""
import threading
import uuid
from unittest.mock import MagicMock, patch

from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from datetime import timedelta

from crm.models import Lead
from whatsapp_integration import tasks
from whatsapp_integration.models import (
    LeadSequenceEnrollment,
    SequenceEnrollmentStatusEnum,
    SequenceStepDelivery,
    SequenceStepDeliveryStatusEnum,
    WhatsAppSequence,
    WhatsAppSequenceStep,
)
from whatsapp_integration.services.laravel_adapter import LaravelAdapterError

TENANT = uuid.UUID('aaaaaaaa-9999-4999-8999-aaaaaaaaaaaa')
OWNER = uuid.UUID('bbbbbbbb-8888-4888-8888-bbbbbbbbbbbb')

WINDOW_OPEN = {
    'reply_window_open': True,
    'reply_window_expires_at': '2026-08-24T10:00:00+00:00',
}
WINDOW_CLOSED = {
    'reply_window_open': False,
    'reply_window_expires_at': '2026-08-20T10:00:00+00:00',
}

STEPPER_ON = dict(
    WHATSAPP_SEQUENCES_ENABLED=True,
    WHATSAPP_SEQUENCE_MAX_ATTEMPTS=3,
    WHATSAPP_SEQUENCE_RETRY_MINUTES=0,
)


def _mock_adapter(window=None, send_result=None, send_side_effect=None):
    """A stand-in for LaravelWhatsAppAdapter that can never reach the network."""
    adapter = MagicMock()
    adapter.get_chat_history.return_value = dict(window or WINDOW_OPEN)
    if send_side_effect is not None:
        adapter.send_message.side_effect = send_side_effect
    else:
        adapter.send_message.return_value = send_result or {'wa_message_id': 'wamid.TEST123'}
    return adapter


class SequenceFixtureMixin:
    def build_fixture(self, step_specs=None, stop_on_reply=True):
        self.lead = Lead.objects.create(
            tenant_id=TENANT,
            name='Asha Menon',
            phone='9876543210',
            company='Menon Dental',
            owner_user_id=OWNER,
        )
        self.sequence = WhatsAppSequence.objects.create(
            tenant_id=TENANT,
            name='Dental Follow-Up %s' % uuid.uuid4().hex[:8],
            stop_on_reply=stop_on_reply,
            created_by=OWNER,
        )
        specs = step_specs or [
            (1, 0, 'tpl-intro', {'1': 'name'}),
            (2, 2, 'tpl-followup', {'1': 'name', '2': 'company'}),
        ]
        self.steps = [
            WhatsAppSequenceStep.objects.create(
                sequence=self.sequence,
                step_number=number,
                delay_days=delay,
                template_uid=template_uid,
                template_variable_mapping=mapping,
            )
            for number, delay, template_uid, mapping in specs
        ]
        self.enrollment = LeadSequenceEnrollment.objects.create(
            tenant_id=TENANT,
            lead=self.lead,
            sequence=self.sequence,
            status=SequenceEnrollmentStatusEnum.ACTIVE,
            next_step_at=timezone.now() - timedelta(minutes=5),
            enrolled_by=OWNER,
        )
        return self.enrollment


# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------

class KillSwitchTests(SequenceFixtureMixin, TestCase):
    def setUp(self):
        self.build_fixture()

    @override_settings(WHATSAPP_SEQUENCES_ENABLED=False)
    def test_disabled_claims_nothing_and_sends_nothing(self):
        with patch.object(tasks, 'LaravelWhatsAppAdapter') as adapter_cls:
            result = tasks.step_due_sequence_enrollments()

        self.assertEqual(result, {'enabled': False, 'claimed': 0, 'sent': 0})
        adapter_cls.assert_not_called()
        self.enrollment.refresh_from_db()
        self.assertIsNone(self.enrollment.locked_at)
        self.assertIsNone(self.enrollment.current_step_id)
        self.assertEqual(self.enrollment.status, SequenceEnrollmentStatusEnum.ACTIVE)
        self.assertEqual(SequenceStepDelivery.objects.count(), 0)

    def test_default_setting_is_off(self):
        """A fresh deploy must not start firing on its own."""
        from django.conf import settings
        self.assertFalse(getattr(settings, 'WHATSAPP_SEQUENCES_ENABLED', False))


# ---------------------------------------------------------------------------
# Happy path: send, advance, complete
# ---------------------------------------------------------------------------

@override_settings(**STEPPER_ON)
class StepAdvanceTests(SequenceFixtureMixin, TestCase):
    def setUp(self):
        self.build_fixture()

    def test_due_step_sends_and_schedules_the_next_one(self):
        adapter = _mock_adapter()
        with patch.object(tasks, 'LaravelWhatsAppAdapter', return_value=adapter):
            result = tasks.step_due_sequence_enrollments()

        self.assertEqual(result['claimed'], 1)
        self.assertEqual(result['sent'], 1)

        adapter.send_message.assert_called_once()
        kwargs = adapter.send_message.call_args.kwargs
        self.assertEqual(kwargs['template_uid'], 'tpl-intro')
        self.assertEqual(kwargs['phone'], '9876543210')
        self.assertEqual(kwargs['digicrm_lead_id'], self.lead.id)
        self.assertEqual(
            kwargs['template_components'],
            [{'type': 'body', 'parameters': [{'type': 'text', 'text': 'Asha Menon'}]}],
        )

        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, SequenceEnrollmentStatusEnum.ACTIVE)
        self.assertEqual(self.enrollment.current_step_id, self.steps[0].id)
        self.assertIsNone(self.enrollment.locked_at)
        self.assertEqual(self.enrollment.attempt_count, 0)
        # Step 2 has delay_days=2.
        self.assertGreater(self.enrollment.next_step_at, timezone.now() + timedelta(days=1))

        delivery = SequenceStepDelivery.objects.get()
        self.assertEqual(delivery.status, SequenceStepDeliveryStatusEnum.SENT)
        self.assertEqual(delivery.wa_message_id, 'wamid.TEST123')
        self.assertEqual(delivery.step_id, self.steps[0].id)
        self.assertEqual(delivery.run_number, 1)

        self.lead.refresh_from_db()
        self.assertIsNotNone(self.lead.last_contacted_at)

    def test_last_step_completes_the_enrollment(self):
        self.enrollment.current_step = self.steps[0]
        self.enrollment.save(update_fields=['current_step'])

        adapter = _mock_adapter()
        with patch.object(tasks, 'LaravelWhatsAppAdapter', return_value=adapter):
            tasks.step_due_sequence_enrollments()

        self.assertEqual(adapter.send_message.call_args.kwargs['template_uid'], 'tpl-followup')
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, SequenceEnrollmentStatusEnum.COMPLETED)
        self.assertIsNotNone(self.enrollment.completed_at)
        self.assertIsNone(self.enrollment.next_step_at)

        # A completed enrollment is not picked up again.
        with patch.object(tasks, 'LaravelWhatsAppAdapter', return_value=adapter):
            result = tasks.step_due_sequence_enrollments()
        self.assertEqual(result['claimed'], 0)
        self.assertEqual(adapter.send_message.call_count, 1)

    def test_enrollment_with_no_steps_completes_without_sending(self):
        WhatsAppSequenceStep.objects.filter(sequence=self.sequence).delete()
        adapter = _mock_adapter()
        with patch.object(tasks, 'LaravelWhatsAppAdapter', return_value=adapter):
            tasks.step_due_sequence_enrollments()

        adapter.send_message.assert_not_called()
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, SequenceEnrollmentStatusEnum.COMPLETED)

    def test_not_yet_due_enrollment_is_left_alone(self):
        self.enrollment.next_step_at = timezone.now() + timedelta(hours=1)
        self.enrollment.save(update_fields=['next_step_at'])

        adapter = _mock_adapter()
        with patch.object(tasks, 'LaravelWhatsAppAdapter', return_value=adapter):
            result = tasks.step_due_sequence_enrollments()

        self.assertEqual(result['claimed'], 0)
        adapter.send_message.assert_not_called()

    def test_template_variables_are_ordered_by_position_not_dict_order(self):
        step = self.steps[0]
        step.template_variable_mapping = {'10': 'company', '2': 'name'}
        step.save(update_fields=['template_variable_mapping'])

        components = tasks.resolve_template_components(step, self.lead)
        self.assertEqual(
            components[0]['parameters'],
            [{'type': 'text', 'text': 'Asha Menon'},
             {'type': 'text', 'text': 'Menon Dental'}],
        )


# ---------------------------------------------------------------------------
# stop_on_reply / non-active statuses
# ---------------------------------------------------------------------------

@override_settings(**STEPPER_ON)
class RepliedEnrollmentTests(SequenceFixtureMixin, TestCase):
    def setUp(self):
        self.build_fixture()

    def test_replied_enrollment_is_never_claimed(self):
        self.enrollment.status = SequenceEnrollmentStatusEnum.REPLIED
        self.enrollment.stopped_reason = 'lead_replied'
        self.enrollment.save(update_fields=['status', 'stopped_reason'])

        adapter = _mock_adapter()
        with patch.object(tasks, 'LaravelWhatsAppAdapter', return_value=adapter):
            result = tasks.step_due_sequence_enrollments()

        self.assertEqual(result['claimed'], 0)
        adapter.send_message.assert_not_called()
        self.assertEqual(SequenceStepDelivery.objects.count(), 0)

    def test_reply_landing_after_the_claim_still_stops_the_send(self):
        """
        The inbound webhook flips ACTIVE -> REPLIED with a plain UPDATE. It can
        land between the claim and the send, so status is re-read under the row
        lock and never trusted from the claim query.
        """
        self.enrollment.locked_at = timezone.now()
        self.enrollment.save(update_fields=['locked_at'])
        LeadSequenceEnrollment.objects.filter(pk=self.enrollment.pk).update(
            status=SequenceEnrollmentStatusEnum.REPLIED,
            stopped_reason='lead_replied',
        )

        adapter = _mock_adapter()
        with patch.object(tasks, 'LaravelWhatsAppAdapter', return_value=adapter):
            outcome = tasks._process_enrollment(self.enrollment.pk, timezone.now())

        self.assertEqual(outcome, 'not_active')
        adapter.send_message.assert_not_called()
        self.assertEqual(SequenceStepDelivery.objects.count(), 0)
        self.enrollment.refresh_from_db()
        self.assertIsNone(self.enrollment.locked_at)

    def test_opted_out_and_paused_are_not_claimed(self):
        for status in (SequenceEnrollmentStatusEnum.OPTED_OUT,
                       SequenceEnrollmentStatusEnum.PAUSED):
            LeadSequenceEnrollment.objects.filter(pk=self.enrollment.pk).update(status=status)
            adapter = _mock_adapter()
            with patch.object(tasks, 'LaravelWhatsAppAdapter', return_value=adapter):
                result = tasks.step_due_sequence_enrollments()
            self.assertEqual(result['claimed'], 0, status)
            adapter.send_message.assert_not_called()

    def test_reenrolling_bumps_run_number_so_step_one_can_send_again(self):
        adapter = _mock_adapter()
        with patch.object(tasks, 'LaravelWhatsAppAdapter', return_value=adapter):
            tasks.step_due_sequence_enrollments()
        self.assertEqual(adapter.send_message.call_count, 1)

        # Lead replies, then is enrolled again later.
        LeadSequenceEnrollment.objects.filter(pk=self.enrollment.pk).update(
            status=SequenceEnrollmentStatusEnum.REPLIED,
        )
        self.enrollment.refresh_from_db()
        self.enrollment.restart(timezone.now() - timedelta(minutes=1))

        self.assertEqual(self.enrollment.run_number, 2)
        with patch.object(tasks, 'LaravelWhatsAppAdapter', return_value=adapter):
            tasks.step_due_sequence_enrollments()

        self.assertEqual(adapter.send_message.call_count, 2)
        self.assertEqual(
            adapter.send_message.call_args.kwargs['template_uid'], 'tpl-intro',
        )
        self.assertEqual(
            SequenceStepDelivery.objects.filter(step=self.steps[0]).count(), 2,
        )


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

@override_settings(**STEPPER_ON)
class IdempotencyTests(SequenceFixtureMixin, TestCase):
    def setUp(self):
        self.build_fixture()

    def test_existing_sent_marker_advances_without_resending(self):
        SequenceStepDelivery.objects.create(
            tenant_id=TENANT,
            enrollment=self.enrollment,
            step=self.steps[0],
            run_number=1,
            status=SequenceStepDeliveryStatusEnum.SENT,
            wa_message_id='wamid.EARLIER',
        )

        adapter = _mock_adapter()
        with patch.object(tasks, 'LaravelWhatsAppAdapter', return_value=adapter):
            result = tasks.step_due_sequence_enrollments()

        adapter.send_message.assert_not_called()
        self.assertEqual(result.get('already_sent'), 1)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.current_step_id, self.steps[0].id)

    def test_marker_stuck_in_sending_is_never_resent(self):
        """A worker died mid-send. Re-sending could duplicate a real message."""
        SequenceStepDelivery.objects.create(
            tenant_id=TENANT,
            enrollment=self.enrollment,
            step=self.steps[0],
            run_number=1,
            status=SequenceStepDeliveryStatusEnum.SENDING,
        )

        adapter = _mock_adapter()
        with patch.object(tasks, 'LaravelWhatsAppAdapter', return_value=adapter):
            result = tasks.step_due_sequence_enrollments()

        adapter.send_message.assert_not_called()
        self.assertEqual(result.get('indeterminate'), 1)
        delivery = SequenceStepDelivery.objects.get()
        self.assertEqual(delivery.status, SequenceStepDeliveryStatusEnum.UNKNOWN)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.current_step_id, self.steps[0].id)

    def test_timeout_is_treated_as_indeterminate_and_not_retried(self):
        adapter = _mock_adapter(
            send_side_effect=LaravelAdapterError('Laravel adapter request timed out', 504),
        )
        with patch.object(tasks, 'LaravelWhatsAppAdapter', return_value=adapter):
            result = tasks.step_due_sequence_enrollments()

        self.assertEqual(result.get('indeterminate'), 1)
        delivery = SequenceStepDelivery.objects.get()
        self.assertEqual(delivery.status, SequenceStepDeliveryStatusEnum.UNKNOWN)

        # Second tick must not re-send the same step.
        with patch.object(tasks, 'LaravelWhatsAppAdapter', return_value=adapter):
            tasks.step_due_sequence_enrollments()
        self.assertEqual(adapter.send_message.call_count, 1)


# ---------------------------------------------------------------------------
# Failure, retry, attempt cap
# ---------------------------------------------------------------------------

@override_settings(WHATSAPP_SEQUENCES_ENABLED=True,
                   WHATSAPP_SEQUENCE_MAX_ATTEMPTS=2,
                   WHATSAPP_SEQUENCE_RETRY_MINUTES=0)
class FailureTests(SequenceFixtureMixin, TestCase):
    def setUp(self):
        self.build_fixture()

    def test_failure_retries_then_caps_out_into_paused(self):
        adapter = _mock_adapter(
            send_side_effect=LaravelAdapterError('Template not found for this vendor', 404),
        )

        with patch.object(tasks, 'LaravelWhatsAppAdapter', return_value=adapter):
            tasks.step_due_sequence_enrollments()
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, SequenceEnrollmentStatusEnum.ACTIVE)
        self.assertEqual(self.enrollment.attempt_count, 1)
        self.assertIn('Template not found', self.enrollment.last_error)
        self.assertIsNone(self.enrollment.locked_at)
        self.assertEqual(
            SequenceStepDelivery.objects.get().status,
            SequenceStepDeliveryStatusEnum.FAILED,
        )

        with patch.object(tasks, 'LaravelWhatsAppAdapter', return_value=adapter):
            tasks.step_due_sequence_enrollments()
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, SequenceEnrollmentStatusEnum.PAUSED)
        self.assertEqual(self.enrollment.stopped_reason, 'max_send_attempts_exceeded')
        self.assertEqual(self.enrollment.attempt_count, 2)
        self.assertEqual(adapter.send_message.call_count, 2)

        # Capped out means it stops spinning: no third attempt, ever.
        with patch.object(tasks, 'LaravelWhatsAppAdapter', return_value=adapter):
            result = tasks.step_due_sequence_enrollments()
        self.assertEqual(result['claimed'], 0)
        self.assertEqual(adapter.send_message.call_count, 2)

    def test_step_with_no_template_fails_permanently_on_the_first_attempt(self):
        step = self.steps[0]
        step.template_uid = ''
        step.save(update_fields=['template_uid'])

        adapter = _mock_adapter(window=WINDOW_OPEN)
        with patch.object(tasks, 'LaravelWhatsAppAdapter', return_value=adapter):
            tasks.step_due_sequence_enrollments()

        adapter.send_message.assert_not_called()
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, SequenceEnrollmentStatusEnum.PAUSED)
        self.assertEqual(self.enrollment.stopped_reason, 'step_misconfigured')
        self.assertIn('no template_uid configured', self.enrollment.last_error)

    def test_blank_template_variable_fails_with_a_clear_error(self):
        self.lead.company = ''
        self.lead.save(update_fields=['company'])
        step = self.steps[0]
        step.template_variable_mapping = {'1': 'name', '2': 'company'}
        step.save(update_fields=['template_variable_mapping'])

        adapter = _mock_adapter()
        with patch.object(tasks, 'LaravelWhatsAppAdapter', return_value=adapter):
            tasks.step_due_sequence_enrollments()

        adapter.send_message.assert_not_called()
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, SequenceEnrollmentStatusEnum.PAUSED)
        self.assertIn('{{2}}', self.enrollment.last_error)
        self.assertIn('company', self.enrollment.last_error)


# ---------------------------------------------------------------------------
# 24-hour session window
# ---------------------------------------------------------------------------

@override_settings(**STEPPER_ON)
class ReplyWindowTests(SequenceFixtureMixin, TestCase):
    def setUp(self):
        self.build_fixture()

    def test_closed_window_still_sends_because_a_step_is_a_template(self):
        adapter = _mock_adapter(window=WINDOW_CLOSED)
        with patch.object(tasks, 'LaravelWhatsAppAdapter', return_value=adapter):
            tasks.step_due_sequence_enrollments()

        adapter.send_message.assert_called_once()
        delivery = SequenceStepDelivery.objects.get()
        self.assertEqual(delivery.status, SequenceStepDeliveryStatusEnum.SENT)
        self.assertEqual(delivery.send_mode, 'TEMPLATE')
        self.assertIs(delivery.reply_window_open, False)
        self.assertEqual(delivery.reply_window_expires_at, '2026-08-20T10:00:00+00:00')

    def test_closed_window_with_no_template_fails_with_a_clear_error(self):
        step = self.steps[0]
        step.template_uid = ''
        step.save(update_fields=['template_uid'])

        adapter = _mock_adapter(window=WINDOW_CLOSED)
        with patch.object(tasks, 'LaravelWhatsAppAdapter', return_value=adapter):
            tasks.step_due_sequence_enrollments()

        adapter.send_message.assert_not_called()
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, SequenceEnrollmentStatusEnum.PAUSED)
        self.assertIn('24-hour WhatsApp session window', self.enrollment.last_error)
        self.assertIn('Configure a template', self.enrollment.last_error)
        # Not silently dropped: the failure is on the delivery row too.
        delivery = SequenceStepDelivery.objects.get()
        self.assertEqual(delivery.status, SequenceStepDeliveryStatusEnum.FAILED)
        self.assertIn('24-hour', delivery.last_error)

    def test_open_window_is_recorded_on_the_delivery(self):
        adapter = _mock_adapter(window=WINDOW_OPEN)
        with patch.object(tasks, 'LaravelWhatsAppAdapter', return_value=adapter):
            tasks.step_due_sequence_enrollments()

        delivery = SequenceStepDelivery.objects.get()
        self.assertIs(delivery.reply_window_open, True)
        self.assertEqual(delivery.reply_window_expires_at, '2026-08-24T10:00:00+00:00')

    def test_window_lookup_failure_does_not_block_a_template_send(self):
        adapter = _mock_adapter()
        adapter.get_chat_history.side_effect = LaravelAdapterError('boom', 502)

        with patch.object(tasks, 'LaravelWhatsAppAdapter', return_value=adapter):
            tasks.step_due_sequence_enrollments()

        adapter.send_message.assert_called_once()
        delivery = SequenceStepDelivery.objects.get()
        self.assertEqual(delivery.status, SequenceStepDeliveryStatusEnum.SENT)
        self.assertIsNone(delivery.reply_window_open)


# ---------------------------------------------------------------------------
# Concurrency: the property that matters most
# ---------------------------------------------------------------------------

@override_settings(**STEPPER_ON)
class ConcurrentClaimTests(SequenceFixtureMixin, TransactionTestCase):
    """
    Needs TransactionTestCase: TestCase wraps each test in a transaction that
    is never committed, so a second connection could not observe the claim.
    """
    def setUp(self):
        self.build_fixture()

    def test_row_locked_by_a_peer_is_skipped_not_blocked_on(self):
        holding = threading.Event()
        release = threading.Event()

        def hold_lock():
            try:
                with transaction.atomic():
                    list(
                        LeadSequenceEnrollment.objects
                        .select_for_update()
                        .filter(pk=self.enrollment.pk)
                    )
                    holding.set()
                    release.wait(timeout=10)
            finally:
                connection.close()

        worker = threading.Thread(target=hold_lock)
        worker.start()
        try:
            self.assertTrue(holding.wait(timeout=10))
            adapter = _mock_adapter()
            with patch.object(tasks, 'LaravelWhatsAppAdapter', return_value=adapter):
                result = tasks.step_due_sequence_enrollments()
            # skip_locked: we stepped over it instead of queueing behind the
            # lock and then sending a duplicate once it cleared.
            self.assertEqual(result['claimed'], 0)
            adapter.send_message.assert_not_called()
        finally:
            release.set()
            worker.join(timeout=10)

    def test_two_concurrent_workers_send_the_step_exactly_once(self):
        sends = []
        sends_lock = threading.Lock()
        start = threading.Barrier(2)

        def fake_adapter_factory(*args, **kwargs):
            adapter = MagicMock()
            adapter.get_chat_history.return_value = dict(WINDOW_OPEN)

            def send(**send_kwargs):
                with sends_lock:
                    sends.append(send_kwargs)
                return {'wa_message_id': 'wamid.%s' % len(sends)}

            adapter.send_message.side_effect = send
            return adapter

        results = []

        def run_worker():
            try:
                start.wait(timeout=15)
                results.append(tasks.step_due_sequence_enrollments())
            except Exception as exc:  # surfaced by the assertions below
                results.append(exc)
            finally:
                connection.close()

        # Patched once, on the main thread: two threads racing patch/unpatch
        # could otherwise restore the real adapter class mid-test.
        with patch.object(tasks, 'LaravelWhatsAppAdapter', fake_adapter_factory):
            threads = [threading.Thread(target=run_worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

        for item in results:
            self.assertNotIsInstance(item, Exception, item)

        # The whole point of the exercise.
        self.assertEqual(len(sends), 1, sends)
        self.assertEqual(SequenceStepDelivery.objects.count(), 1)
        self.assertEqual(
            SequenceStepDelivery.objects.get().status,
            SequenceStepDeliveryStatusEnum.SENT,
        )
        self.assertEqual(sum(r.get('claimed', 0) for r in results), 1)

    def test_a_claimed_row_is_not_reclaimed_by_the_next_poll(self):
        now = timezone.now()
        first = tasks._claim_due_enrollments(now, 100)
        second = tasks._claim_due_enrollments(now, 100)

        self.assertEqual(first, [self.enrollment.pk])
        self.assertEqual(second, [])

        self.enrollment.refresh_from_db()
        self.assertIsNotNone(self.enrollment.locked_at)

    def test_a_stale_claim_is_released(self):
        LeadSequenceEnrollment.objects.filter(pk=self.enrollment.pk).update(
            locked_at=timezone.now() - timedelta(hours=2),
        )
        claimed = tasks._claim_due_enrollments(timezone.now(), 100)
        self.assertEqual(claimed, [self.enrollment.pk])
