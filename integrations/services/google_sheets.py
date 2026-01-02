"""
Google Sheets Service Layer

Handles all interactions with Google Sheets API:
- Listing spreadsheets
- Reading sheet data
- Detecting new rows
- Managing sheet metadata
"""

import logging
from typing import Dict, List, Optional, Any
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from django.core.cache import cache

from integrations.utils.oauth import get_oauth_handler
from integrations.utils.encryption import decrypt_token

logger = logging.getLogger(__name__)


class GoogleSheetsError(Exception):
    """Custom exception for Google Sheets errors"""
    pass


class GoogleSheetsService:
    """
    Service for interacting with Google Sheets API.

    Provides methods for:
    - Listing user's spreadsheets
    - Reading sheet data
    - Detecting new rows
    - Getting sheet metadata
    """

    def __init__(self, access_token: str, refresh_token: str = None):
        """
        Initialize Google Sheets service with credentials.

        Args:
            access_token: Google OAuth access token (can be encrypted)
            refresh_token: Google OAuth refresh token (can be encrypted)
        """
        self.access_token = access_token
        self.refresh_token = refresh_token
        self._service = None
        self._drive_service = None

    def _get_credentials(self) -> Credentials:
        """
        Get Google credentials from stored tokens.

        Returns:
            Credentials: Google OAuth credentials
        """
        oauth_handler = get_oauth_handler()
        return oauth_handler.get_credentials(
            self.access_token,
            self.refresh_token
        )

    def _get_sheets_service(self):
        """
        Get or create Google Sheets API service.

        Returns:
            Resource: Google Sheets API service
        """
        if self._service is None:
            credentials = self._get_credentials()
            self._service = build('sheets', 'v4', credentials=credentials)

        return self._service

    def _get_drive_service(self):
        """
        Get or create Google Drive API service.

        Returns:
            Resource: Google Drive API service
        """
        if self._drive_service is None:
            credentials = self._get_credentials()
            self._drive_service = build('drive', 'v3', credentials=credentials)

        return self._drive_service

    def list_spreadsheets(self, page_size: int = 100) -> List[Dict]:
        """
        List all spreadsheets accessible to the user.

        Args:
            page_size: Maximum number of spreadsheets to return

        Returns:
            List[Dict]: List of spreadsheet metadata

        Raises:
            GoogleSheetsError: If listing fails
        """
        try:
            drive_service = self._get_drive_service()

            # Query for Google Sheets files
            results = drive_service.files().list(
                pageSize=page_size,
                q="mimeType='application/vnd.google-apps.spreadsheet'",
                fields="files(id, name, createdTime, modifiedTime, owners, webViewLink)",
                orderBy="modifiedTime desc"
            ).execute()

            spreadsheets = results.get('files', [])

            logger.info(f"Found {len(spreadsheets)} spreadsheets")
            return spreadsheets

        except HttpError as e:
            logger.error(f"Failed to list spreadsheets: {e}")
            raise GoogleSheetsError(f"Failed to list spreadsheets: {e}")

        except Exception as e:
            logger.error(f"Unexpected error listing spreadsheets: {e}")
            raise GoogleSheetsError(f"Failed to list spreadsheets: {e}")

    def get_spreadsheet_metadata(self, spreadsheet_id: str) -> Dict:
        """
        Get metadata about a specific spreadsheet.

        Args:
            spreadsheet_id: The spreadsheet ID

        Returns:
            Dict: Spreadsheet metadata including sheets

        Raises:
            GoogleSheetsError: If fetching metadata fails
        """
        try:
            sheets_service = self._get_sheets_service()

            # Get spreadsheet metadata
            spreadsheet = sheets_service.spreadsheets().get(
                spreadsheetId=spreadsheet_id,
                fields="properties,sheets(properties)"
            ).execute()

            logger.info(f"Retrieved metadata for spreadsheet: {spreadsheet_id}")
            return spreadsheet

        except HttpError as e:
            logger.error(f"Failed to get spreadsheet metadata: {e}")
            raise GoogleSheetsError(f"Failed to get spreadsheet metadata: {e}")

        except Exception as e:
            logger.error(f"Unexpected error getting spreadsheet metadata: {e}")
            raise GoogleSheetsError(f"Failed to get spreadsheet metadata: {e}")

    def list_sheets(self, spreadsheet_id: str) -> List[Dict]:
        """
        List all sheets in a spreadsheet.

        Args:
            spreadsheet_id: The spreadsheet ID

        Returns:
            List[Dict]: List of sheet metadata

        Raises:
            GoogleSheetsError: If listing sheets fails
        """
        try:
            metadata = self.get_spreadsheet_metadata(spreadsheet_id)
            sheets = metadata.get('sheets', [])

            sheet_list = []
            for sheet in sheets:
                props = sheet.get('properties', {})
                sheet_list.append({
                    'sheet_id': props.get('sheetId'),
                    'title': props.get('title'),
                    'index': props.get('index'),
                    'row_count': props.get('gridProperties', {}).get('rowCount'),
                    'column_count': props.get('gridProperties', {}).get('columnCount'),
                })

            logger.info(f"Found {len(sheet_list)} sheets in spreadsheet {spreadsheet_id}")
            return sheet_list

        except Exception as e:
            logger.error(f"Failed to list sheets: {e}")
            raise GoogleSheetsError(f"Failed to list sheets: {e}")

    def read_sheet_data(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        range_notation: str = None,
        include_headers: bool = True
    ) -> Dict[str, Any]:
        """
        Read data from a specific sheet.

        Args:
            spreadsheet_id: The spreadsheet ID
            sheet_name: Name of the sheet to read
            range_notation: Optional A1 notation range (e.g., "A1:Z100")
            include_headers: Whether to include headers in response

        Returns:
            Dict: Sheet data with headers and rows

        Raises:
            GoogleSheetsError: If reading data fails
        """
        try:
            sheets_service = self._get_sheets_service()

            # Build range string
            if range_notation:
                range_str = f"{sheet_name}!{range_notation}"
            else:
                range_str = sheet_name

            # Read data
            result = sheets_service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range=range_str,
                valueRenderOption='UNFORMATTED_VALUE',
                dateTimeRenderOption='FORMATTED_STRING'
            ).execute()

            values = result.get('values', [])

            if not values:
                return {
                    'headers': [],
                    'rows': [],
                    'total_rows': 0
                }

            # Separate headers and data rows
            headers = values[0] if include_headers and values else []
            data_rows = values[1:] if include_headers and len(values) > 1 else values

            # Normalize rows to have same length as headers
            normalized_rows = []
            header_count = len(headers) if headers else (max(len(row) for row in data_rows) if data_rows else 0)

            for row in data_rows:
                # Pad row to match header count
                normalized_row = row + [''] * (header_count - len(row))
                normalized_rows.append(normalized_row)

            logger.info(
                f"Read {len(normalized_rows)} rows from {sheet_name} "
                f"in spreadsheet {spreadsheet_id}"
            )

            return {
                'headers': headers,
                'rows': normalized_rows,
                'total_rows': len(normalized_rows),
                'range': result.get('range'),
            }

        except HttpError as e:
            logger.error(f"Failed to read sheet data: {e}")
            raise GoogleSheetsError(f"Failed to read sheet data: {e}")

        except Exception as e:
            logger.error(f"Unexpected error reading sheet data: {e}")
            raise GoogleSheetsError(f"Failed to read sheet data: {e}")

    def get_new_rows(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        last_row_number: int = 0,
        headers: List[str] = None
    ) -> List[Dict]:
        """
        Get new rows added since last check.

        Args:
            spreadsheet_id: The spreadsheet ID
            sheet_name: Name of the sheet
            last_row_number: Last processed row number (0-indexed, excluding header)
            headers: Optional list of headers (will fetch if not provided)

        Returns:
            List[Dict]: List of new rows as dictionaries

        Raises:
            GoogleSheetsError: If fetching new rows fails
        """
        try:
            # Read all data
            data = self.read_sheet_data(
                spreadsheet_id=spreadsheet_id,
                sheet_name=sheet_name,
                include_headers=True
            )

            if not data['rows']:
                return []

            # Use provided headers or get from data
            sheet_headers = headers or data['headers']

            # Get rows after last_row_number
            new_rows = data['rows'][last_row_number:]

            # Convert to dictionaries
            new_rows_dict = []
            for idx, row in enumerate(new_rows):
                row_number = last_row_number + idx + 1  # 1-indexed, excluding header
                row_dict = {
                    '_row_number': row_number,
                    '_spreadsheet_id': spreadsheet_id,
                    '_sheet_name': sheet_name,
                }

                # Map row values to headers
                for i, header in enumerate(sheet_headers):
                    value = row[i] if i < len(row) else ''
                    row_dict[header] = value

                new_rows_dict.append(row_dict)

            logger.info(
                f"Found {len(new_rows_dict)} new rows in {sheet_name} "
                f"(after row {last_row_number})"
            )

            return new_rows_dict

        except Exception as e:
            logger.error(f"Failed to get new rows: {e}")
            raise GoogleSheetsError(f"Failed to get new rows: {e}")

    def get_row_count(self, spreadsheet_id: str, sheet_name: str) -> int:
        """
        Get the total number of rows in a sheet (excluding header).

        Args:
            spreadsheet_id: The spreadsheet ID
            sheet_name: Name of the sheet

        Returns:
            int: Number of data rows (excluding header)

        Raises:
            GoogleSheetsError: If getting row count fails
        """
        try:
            data = self.read_sheet_data(
                spreadsheet_id=spreadsheet_id,
                sheet_name=sheet_name,
                include_headers=True
            )

            return data['total_rows']

        except Exception as e:
            logger.error(f"Failed to get row count: {e}")
            raise GoogleSheetsError(f"Failed to get row count: {e}")

    def validate_sheet_access(self, spreadsheet_id: str, sheet_name: str = None) -> bool:
        """
        Validate that the user has access to a spreadsheet and optional sheet.

        Args:
            spreadsheet_id: The spreadsheet ID
            sheet_name: Optional sheet name to validate

        Returns:
            bool: True if access is valid

        Raises:
            GoogleSheetsError: If validation fails
        """
        try:
            metadata = self.get_spreadsheet_metadata(spreadsheet_id)

            if sheet_name:
                sheets = self.list_sheets(spreadsheet_id)
                sheet_names = [s['title'] for s in sheets]

                if sheet_name not in sheet_names:
                    raise GoogleSheetsError(f"Sheet '{sheet_name}' not found in spreadsheet")

            return True

        except Exception as e:
            logger.error(f"Failed to validate sheet access: {e}")
            raise GoogleSheetsError(f"Failed to validate sheet access: {e}")


def create_sheets_service(connection) -> GoogleSheetsService:
    """
    Create a GoogleSheetsService from a Connection model instance.

    Args:
        connection: Connection model instance

    Returns:
        GoogleSheetsService: Configured service instance

    Raises:
        GoogleSheetsError: If service creation fails
    """
    try:
        # Decrypt tokens
        access_token = decrypt_token(connection.access_token_encrypted)
        refresh_token = decrypt_token(connection.refresh_token_encrypted) if connection.refresh_token_encrypted else None

        # Create service
        service = GoogleSheetsService(
            access_token=access_token,
            refresh_token=refresh_token
        )

        return service

    except Exception as e:
        logger.error(f"Failed to create Google Sheets service: {e}")
        raise GoogleSheetsError(f"Failed to create service: {e}")
