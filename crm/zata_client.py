"""
Zata S3 storage client for proxying file uploads.
"""
import requests
from django.conf import settings


def upload_to_zata(file_obj, folder_id, filename):
    """
    Upload a file to Zata storage under the given folder.

    Args:
        file_obj: Django InMemoryUploadedFile or TemporaryUploadedFile
        folder_id: UUID string of the target Zata folder
        filename: Original filename to send to Zata

    Returns:
        dict with 'id' (UUID) and 'download_url' (str) from Zata's response
    """
    api_url = getattr(settings, 'ZATA_API_URL', '').rstrip('/')
    api_token = getattr(settings, 'ZATA_API_TOKEN', '')

    url = f"{api_url}/api/video-folders/{folder_id}/videos"
    headers = {"Authorization": f"Bearer {api_token}"}

    content_type = getattr(file_obj, 'content_type', 'application/octet-stream')
    response = requests.post(
        url,
        headers=headers,
        files={"video": (filename, file_obj, content_type)},
        timeout=120,
    )
    response.raise_for_status()

    data = response.json()
    video_data = data.get("video", {})
    return {
        "id": video_data.get("id"),
        "download_url": video_data.get("download_url"),
    }


def delete_from_zata(video_id):
    """
    Delete a video record from Zata storage.

    Args:
        video_id: UUID string of the Zata video to delete

    Returns:
        True on success, raises on HTTP error
    """
    api_url = getattr(settings, 'ZATA_API_URL', '').rstrip('/')
    api_token = getattr(settings, 'ZATA_API_TOKEN', '')

    url = f"{api_url}/api/videos/{video_id}"
    headers = {"Authorization": f"Bearer {api_token}"}

    response = requests.delete(url, headers=headers, timeout=30)
    response.raise_for_status()
    return True
