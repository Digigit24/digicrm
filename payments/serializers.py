from rest_framework import serializers
from .models import Payment
from common.mixins import TenantMixin


class PaymentSerializer(TenantMixin):
    """
    Serialize payment and financial transaction records linked to leads.

    Agents use this schema to create and inspect invoices, advances, refunds,
    and other payment events connected to CRM leads.
    """
    lead_name = serializers.CharField(
        source='lead.name',
        read_only=True,
        help_text='Display name of the linked lead. Read-only.'
    )
    
    class Meta:
        model = Payment
        fields = [
            'id', 'lead', 'lead_name', 'type', 'amount', 'currency',
            'method', 'reference_no', 'notes', 'date', 'status',
            'attachment_url', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for this payment. Read-only.'},
            'lead': {'help_text': 'Numeric ID of the lead this payment is related to.'},
            'type': {'help_text': 'Payment type. Valid values are INVOICE, REFUND, ADVANCE, or OTHER.'},
            'amount': {'help_text': 'Payment amount using decimal notation with up to two decimal places.'},
            'currency': {'help_text': 'Currency code or label for the amount, for example INR, USD, or EUR.'},
            'method': {'help_text': 'Optional payment method, such as cash, bank transfer, UPI, card, cheque, or online gateway.'},
            'reference_no': {'help_text': 'Optional payment reference number, transaction ID, invoice number, or receipt number.'},
            'notes': {'help_text': 'Optional free-text notes about this payment or transaction.'},
            'date': {'help_text': 'Payment date and time in ISO 8601 date-time format.'},
            'status': {'help_text': 'Payment status. Valid values are PENDING, CLEARED, FAILED, or CANCELLED.'},
            'attachment_url': {'help_text': 'Optional URL for a receipt, invoice, proof of payment, or related attachment.'},
            'created_at': {'help_text': 'Timestamp when this payment record was created, in ISO 8601 date-time format. Read-only.'},
            'updated_at': {'help_text': 'Timestamp when this payment record was last updated, in ISO 8601 date-time format. Read-only.'},
        }


class PaymentListSerializer(TenantMixin):
    """
    Serialize compact payment records for payment lists and summaries.

    Agents use this schema when browsing many payments without full notes or
    attachment details.
    """
    lead_name = serializers.CharField(
        source='lead.name',
        read_only=True,
        help_text='Display name of the linked lead. Read-only.'
    )
    
    class Meta:
        model = Payment
        fields = [
            'id', 'lead', 'lead_name', 'type', 'amount', 'currency',
            'date', 'status', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for this payment. Read-only.'},
            'lead': {'help_text': 'Numeric ID of the lead this payment is related to.'},
            'type': {'help_text': 'Payment type. Valid values are INVOICE, REFUND, ADVANCE, or OTHER.'},
            'amount': {'help_text': 'Payment amount using decimal notation with up to two decimal places.'},
            'currency': {'help_text': 'Currency code or label for the amount, for example INR, USD, or EUR.'},
            'date': {'help_text': 'Payment date and time in ISO 8601 date-time format.'},
            'status': {'help_text': 'Payment status. Valid values are PENDING, CLEARED, FAILED, or CANCELLED.'},
            'created_at': {'help_text': 'Timestamp when this payment record was created, in ISO 8601 date-time format. Read-only.'},
        }
