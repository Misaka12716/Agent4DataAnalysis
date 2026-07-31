from reader.handlers.table import digest_table_file
from reader.handlers.image import digest_image_file
from reader.handlers.text import digest_text_file
from reader.handlers.fallback import digest_binary_file
from reader.handlers.document_pdf import digest_pdf_file
from reader.handlers.document_docx import digest_docx_file
from reader.handlers.imaging_dicom import digest_dicom_file

__all__ = [
    "digest_table_file",
    "digest_image_file",
    "digest_text_file",
    "digest_binary_file",
    "digest_pdf_file",
    "digest_docx_file",
    "digest_dicom_file",
]
