"""
Workflow Engine Service

Core automation engine that executes workflows:
- Triggers workflows based on events
- Transforms data using field mappings
- Executes actions (create leads, tasks, etc.)
- Handles errors and retries
- Logs execution details
"""

import logging
import hashlib
import uuid
import traceback
from typing import Dict, List, Any, Optional
from datetime import datetime
from django.utils import timezone
from django.db import transaction
from django.core.exceptions import ValidationError

from integrations.models import (
    Workflow, WorkflowTrigger, WorkflowAction, WorkflowMapping,
    ExecutionLog, DuplicateDetectionCache,
    TriggerTypeEnum, ActionTypeEnum, ExecutionStatusEnum
)
from integrations.services.google_sheets import create_sheets_service, GoogleSheetsError
from crm.models import Lead, LeadStatus

logger = logging.getLogger(__name__)


class WorkflowEngineError(Exception):
    """Custom exception for workflow engine errors"""
    pass


class WorkflowEngine:
    """
    Main workflow execution engine.

    Handles:
    - Trigger detection
    - Data transformation
    - Action execution
    - Error handling and retry logic
    - Execution logging
    """

    def __init__(self, workflow: Workflow):
        """
        Initialize workflow engine for a specific workflow.

        Args:
            workflow: The Workflow model instance to execute
        """
        self.workflow = workflow
        self.tenant_id = workflow.tenant_id
        self.execution_log = None
        self.execution_steps = []

    def _log_step(self, step_name: str, status: str, details: Any = None):
        """
        Log an execution step.

        Args:
            step_name: Name of the step
            status: Status (success, error, info)
            details: Additional details
        """
        step = {
            'timestamp': timezone.now().isoformat(),
            'step': step_name,
            'status': status,
            'details': details
        }
        self.execution_steps.append(step)
        logger.info(f"[{self.workflow.name}] {step_name}: {status}")

    def _create_execution_log(self, trigger_data: Dict = None) -> ExecutionLog:
        """
        Create a new execution log entry.

        Args:
            trigger_data: Data that triggered the workflow

        Returns:
            ExecutionLog: Created execution log instance
        """
        execution_log = ExecutionLog.objects.create(
            tenant_id=self.tenant_id,
            workflow=self.workflow,
            execution_id=uuid.uuid4(),
            status=ExecutionStatusEnum.PENDING,
            trigger_data=trigger_data
        )

        self.execution_log = execution_log
        return execution_log

    def check_trigger(self) -> List[Dict]:
        """
        Check if workflow trigger conditions are met.

        Returns:
            List[Dict]: List of trigger data (new rows, webhook payloads, etc.)

        Raises:
            WorkflowEngineError: If trigger check fails
        """
        try:
            if not hasattr(self.workflow, 'trigger'):
                raise WorkflowEngineError("Workflow has no trigger configured")

            trigger = self.workflow.trigger
            trigger_config = trigger.trigger_config

            if trigger.trigger_type == TriggerTypeEnum.NEW_ROW:
                return self._check_new_row_trigger(trigger, trigger_config)

            elif trigger.trigger_type == TriggerTypeEnum.MANUAL:
                # Manual triggers don't auto-check
                return []

            else:
                logger.warning(f"Unsupported trigger type: {trigger.trigger_type}")
                return []

        except Exception as e:
            logger.error(f"Trigger check failed: {e}")
            raise WorkflowEngineError(f"Trigger check failed: {e}")

    def _check_new_row_trigger(
        self,
        trigger: WorkflowTrigger,
        config: Dict
    ) -> List[Dict]:
        """
        Check for new rows in Google Sheets.

        Args:
            trigger: The WorkflowTrigger instance
            config: Trigger configuration

        Returns:
            List[Dict]: List of new row data
        """
        try:
            # Get configuration
            spreadsheet_id = config.get('spreadsheet_id')
            sheet_name = config.get('sheet_name')

            if not spreadsheet_id or not sheet_name:
                raise WorkflowEngineError("Missing spreadsheet_id or sheet_name in trigger config")

            # Get last processed row number
            last_processed = trigger.last_processed_record or {}
            last_row_number = last_processed.get('row_number', 0)

            # Create Google Sheets service
            sheets_service = create_sheets_service(self.workflow.connection)

            # Get new rows
            new_rows = sheets_service.get_new_rows(
                spreadsheet_id=spreadsheet_id,
                sheet_name=sheet_name,
                last_row_number=last_row_number
            )

            if new_rows:
                # Update last processed record
                last_row = new_rows[-1]
                trigger.last_processed_record = {
                    'row_number': last_row['_row_number'],
                    'checked_at': timezone.now().isoformat()
                }
                trigger.last_checked_at = timezone.now()
                trigger.save(update_fields=['last_processed_record', 'last_checked_at', 'updated_at'])

                self._log_step(
                    'check_trigger',
                    'success',
                    f'Found {len(new_rows)} new rows'
                )

            return new_rows

        except GoogleSheetsError as e:
            logger.error(f"Google Sheets error while checking trigger: {e}")
            raise WorkflowEngineError(f"Failed to check trigger: {e}")

        except Exception as e:
            logger.error(f"Error checking new row trigger: {e}")
            raise WorkflowEngineError(f"Failed to check trigger: {e}")

    def transform_data(
        self,
        source_data: Dict,
        action: WorkflowAction
    ) -> Dict:
        """
        Transform source data using field mappings.

        Args:
            source_data: Raw data from trigger
            action: WorkflowAction with field mappings

        Returns:
            Dict: Transformed data ready for action execution
        """
        try:
            transformed_data = {}

            # Get all field mappings for this action
            mappings = action.field_mappings.all()

            for mapping in mappings:
                source_field = mapping.source_field
                dest_field = mapping.destination_field

                # Get source value
                value = source_data.get(source_field)

                # Apply transformations if configured
                if mapping.transformation:
                    value = self._apply_transformation(value, mapping.transformation)

                # Use default value if source is empty and required
                if not value and mapping.default_value:
                    value = mapping.default_value

                # Validate if required
                if mapping.is_required and not value:
                    raise ValidationError(f"Required field '{dest_field}' is missing")

                # Apply validation rules if configured
                if mapping.validation_rules and value:
                    self._validate_field(value, mapping.validation_rules, dest_field)

                # Store in transformed data
                transformed_data[dest_field] = value

            self._log_step(
                'transform_data',
                'success',
                f'Transformed {len(mappings)} fields'
            )

            return transformed_data

        except Exception as e:
            logger.error(f"Data transformation failed: {e}")
            raise WorkflowEngineError(f"Data transformation failed: {e}")

    def _apply_transformation(self, value: Any, transformation: Dict) -> Any:
        """
        Apply transformation rules to a value.

        Args:
            value: The value to transform
            transformation: Transformation rules

        Returns:
            Any: Transformed value
        """
        if not value:
            return value

        # Convert to string for transformations
        str_value = str(value)

        # Apply transformations
        if transformation.get('trim'):
            str_value = str_value.strip()

        if transformation.get('lowercase'):
            str_value = str_value.lower()

        if transformation.get('uppercase'):
            str_value = str_value.upper()

        if transformation.get('remove_spaces'):
            str_value = str_value.replace(' ', '')

        # Add more transformations as needed

        return str_value

    def _validate_field(self, value: Any, rules: Dict, field_name: str):
        """
        Validate a field value against rules.

        Args:
            value: The value to validate
            rules: Validation rules
            field_name: Name of the field (for error messages)

        Raises:
            ValidationError: If validation fails
        """
        str_value = str(value)

        # Min length
        if 'min_length' in rules:
            if len(str_value) < rules['min_length']:
                raise ValidationError(
                    f"Field '{field_name}' must be at least {rules['min_length']} characters"
                )

        # Max length
        if 'max_length' in rules:
            if len(str_value) > rules['max_length']:
                raise ValidationError(
                    f"Field '{field_name}' must be at most {rules['max_length']} characters"
                )

        # Regex pattern
        if 'pattern' in rules:
            import re
            if not re.match(rules['pattern'], str_value):
                raise ValidationError(
                    f"Field '{field_name}' does not match required pattern"
                )

    def execute_action(
        self,
        action: WorkflowAction,
        transformed_data: Dict,
        source_data: Dict
    ) -> Dict:
        """
        Execute a workflow action.

        Args:
            action: WorkflowAction to execute
            transformed_data: Transformed data from mappings
            source_data: Original source data

        Returns:
            Dict: Result of action execution

        Raises:
            WorkflowEngineError: If action execution fails
        """
        try:
            if action.action_type == ActionTypeEnum.CREATE_LEAD:
                return self._execute_create_lead(action, transformed_data, source_data)

            elif action.action_type == ActionTypeEnum.UPDATE_LEAD:
                return self._execute_update_lead(action, transformed_data, source_data)

            else:
                raise WorkflowEngineError(f"Unsupported action type: {action.action_type}")

        except Exception as e:
            logger.error(f"Action execution failed: {e}")
            raise WorkflowEngineError(f"Action execution failed: {e}")

    def _execute_create_lead(
        self,
        action: WorkflowAction,
        data: Dict,
        source_data: Dict
    ) -> Dict:
        """
        Execute CREATE_LEAD action.

        Args:
            action: WorkflowAction instance
            data: Transformed data
            source_data: Original source data

        Returns:
            Dict: Created lead information
        """
        try:
            # Check for duplicates
            source_identifier = self._generate_source_identifier(source_data)

            if self._is_duplicate(source_identifier):
                self._log_step(
                    'create_lead',
                    'skipped',
                    'Duplicate detected - lead already exists'
                )
                return {
                    'status': 'skipped',
                    'reason': 'duplicate',
                    'source_identifier': source_identifier
                }

            # Get action configuration
            action_config = action.action_config or {}

            # Build lead data
            lead_data = {
                'tenant_id': self.tenant_id,
                'name': data.get('name', 'Unnamed Lead'),
                'phone': data.get('phone', ''),
                'email': data.get('email'),
                'company': data.get('company'),
                'title': data.get('title'),
                'source': action_config.get('default_source', 'Integration'),
                'owner_user_id': self.workflow.user_id,
                'assigned_to': data.get('assigned_to') or action_config.get('default_assigned_to'),
                'priority': data.get('priority') or action_config.get('default_priority', 'MEDIUM'),
                'notes': data.get('notes'),
                'address_line1': data.get('address_line1'),
                'address_line2': data.get('address_line2'),
                'city': data.get('city'),
                'state': data.get('state'),
                'country': data.get('country'),
                'postal_code': data.get('postal_code'),
            }

            # Handle status
            if 'status_id' in data:
                lead_data['status_id'] = data['status_id']
            elif action_config.get('default_status_id'):
                lead_data['status_id'] = action_config['default_status_id']

            # Handle custom fields (metadata)
            metadata = {}
            for key, value in data.items():
                if key not in lead_data and not key.startswith('_'):
                    metadata[key] = value

            if metadata:
                lead_data['metadata'] = metadata

            # Create lead
            with transaction.atomic():
                lead = Lead.objects.create(**lead_data)

                # Record in duplicate cache
                self._record_duplicate(source_identifier, 'Lead', lead.id, source_data)

            self._log_step(
                'create_lead',
                'success',
                f'Created lead ID: {lead.id}'
            )

            return {
                'status': 'created',
                'lead_id': lead.id,
                'lead_name': lead.name,
                'lead_phone': lead.phone
            }

        except Exception as e:
            logger.error(f"Failed to create lead: {e}")
            raise WorkflowEngineError(f"Failed to create lead: {e}")

    def _execute_update_lead(
        self,
        action: WorkflowAction,
        data: Dict,
        source_data: Dict
    ) -> Dict:
        """
        Execute UPDATE_LEAD action.

        Args:
            action: WorkflowAction instance
            data: Transformed data
            source_data: Original source data

        Returns:
            Dict: Updated lead information
        """
        # Implementation for updating leads
        # This would find the lead and update it
        raise NotImplementedError("Update lead action not yet implemented")

    def _generate_source_identifier(self, source_data: Dict) -> str:
        """
        Generate unique identifier for source data.

        Args:
            source_data: Original source data

        Returns:
            str: Unique identifier
        """
        # Use spreadsheet_id:sheet_name:row_number as identifier
        spreadsheet_id = source_data.get('_spreadsheet_id', '')
        sheet_name = source_data.get('_sheet_name', '')
        row_number = source_data.get('_row_number', '')

        return f"{spreadsheet_id}:{sheet_name}:{row_number}"

    def _generate_data_hash(self, source_data: Dict) -> str:
        """
        Generate hash of source data for change detection.

        Args:
            source_data: Source data

        Returns:
            str: SHA256 hash
        """
        # Sort keys for consistent hashing
        import json
        data_str = json.dumps(source_data, sort_keys=True)
        return hashlib.sha256(data_str.encode()).hexdigest()

    def _is_duplicate(self, source_identifier: str) -> bool:
        """
        Check if source data has already been processed.

        Args:
            source_identifier: Unique source identifier

        Returns:
            bool: True if duplicate
        """
        return DuplicateDetectionCache.objects.filter(
            workflow=self.workflow,
            source_identifier=source_identifier
        ).exists()

    def _record_duplicate(
        self,
        source_identifier: str,
        object_type: str,
        object_id: int,
        source_data: Dict
    ):
        """
        Record processed data in duplicate cache.

        Args:
            source_identifier: Unique source identifier
            object_type: Type of object created (Lead, Task, etc.)
            object_id: ID of created object
            source_data: Original source data
        """
        DuplicateDetectionCache.objects.create(
            tenant_id=self.tenant_id,
            workflow=self.workflow,
            source_identifier=source_identifier,
            created_object_type=object_type,
            created_object_id=object_id,
            source_data_hash=self._generate_data_hash(source_data)
        )

    def execute_workflow(self, trigger_data_list: List[Dict] = None) -> List[ExecutionLog]:
        """
        Execute the workflow for a list of trigger data.

        Args:
            trigger_data_list: Optional list of trigger data.
                              If None, will check trigger automatically.

        Returns:
            List[ExecutionLog]: List of execution logs

        Raises:
            WorkflowEngineError: If workflow execution fails
        """
        execution_logs = []

        try:
            # Check if workflow is active
            if not self.workflow.is_active:
                logger.warning(f"Workflow {self.workflow.name} is not active")
                return execution_logs

            # Get trigger data
            if trigger_data_list is None:
                trigger_data_list = self.check_trigger()

            if not trigger_data_list:
                logger.info(f"No trigger data for workflow {self.workflow.name}")
                return execution_logs

            # Execute workflow for each trigger data
            for trigger_data in trigger_data_list:
                execution_log = self._execute_single_workflow(trigger_data)
                execution_logs.append(execution_log)

            # Update workflow statistics
            self._update_workflow_stats(execution_logs)

            return execution_logs

        except Exception as e:
            logger.error(f"Workflow execution failed: {e}")
            raise WorkflowEngineError(f"Workflow execution failed: {e}")

    def _execute_single_workflow(self, trigger_data: Dict) -> ExecutionLog:
        """
        Execute workflow for a single trigger data item.

        Args:
            trigger_data: Single trigger data item

        Returns:
            ExecutionLog: Execution log
        """
        # Reset execution steps
        self.execution_steps = []

        # Create execution log
        execution_log = self._create_execution_log(trigger_data)

        try:
            # Mark as running
            execution_log.mark_as_running()

            # Get all actions for this workflow
            actions = self.workflow.actions.all().order_by('order')

            if not actions:
                raise WorkflowEngineError("Workflow has no actions configured")

            result_data = {}

            # Execute each action
            for action in actions:
                # Transform data
                transformed_data = self.transform_data(trigger_data, action)

                # Execute action
                action_result = self.execute_action(action, transformed_data, trigger_data)

                # Store result
                result_data[f'action_{action.id}'] = action_result

            # Mark as success
            execution_log.mark_as_success(
                result_data=result_data,
                execution_steps=self.execution_steps
            )

            logger.info(
                f"Workflow {self.workflow.name} executed successfully "
                f"(execution_id: {execution_log.execution_id})"
            )

        except Exception as e:
            # Mark as failed
            error_traceback = traceback.format_exc()
            execution_log.mark_as_failed(
                error_message=str(e),
                error_traceback=error_traceback
            )

            # Update execution steps
            execution_log.execution_steps = self.execution_steps
            execution_log.save(update_fields=['execution_steps'])

            logger.error(
                f"Workflow {self.workflow.name} execution failed: {e}",
                exc_info=True
            )

        return execution_log

    def _update_workflow_stats(self, execution_logs: List[ExecutionLog]):
        """
        Update workflow statistics after execution.

        Args:
            execution_logs: List of execution logs
        """
        if not execution_logs:
            return

        success_count = sum(1 for log in execution_logs if log.status == ExecutionStatusEnum.SUCCESS)
        failed_count = sum(1 for log in execution_logs if log.status == ExecutionStatusEnum.FAILED)

        self.workflow.total_executions += len(execution_logs)
        self.workflow.successful_executions += success_count
        self.workflow.failed_executions += failed_count
        self.workflow.last_executed_at = timezone.now()
        self.workflow.last_execution_status = execution_logs[-1].status

        self.workflow.save(update_fields=[
            'total_executions',
            'successful_executions',
            'failed_executions',
            'last_executed_at',
            'last_execution_status',
            'updated_at'
        ])


def execute_workflow_by_id(workflow_id: int) -> List[ExecutionLog]:
    """
    Execute a workflow by its ID.

    Args:
        workflow_id: Workflow ID

    Returns:
        List[ExecutionLog]: Execution logs

    Raises:
        WorkflowEngineError: If workflow not found or execution fails
    """
    try:
        workflow = Workflow.objects.select_related('connection', 'trigger').get(
            id=workflow_id,
            is_deleted=False
        )

        engine = WorkflowEngine(workflow)
        return engine.execute_workflow()

    except Workflow.DoesNotExist:
        raise WorkflowEngineError(f"Workflow with ID {workflow_id} not found")

    except Exception as e:
        logger.error(f"Failed to execute workflow {workflow_id}: {e}")
        raise WorkflowEngineError(f"Workflow execution failed: {e}")
