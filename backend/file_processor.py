import base64
import io
import os
from typing import Dict, Any
from PIL import Image
from pypdf import PdfReader
import docx

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
PDF_EXTENSIONS = {".pdf"}
DOCX_EXTENSIONS = {".docx"}
TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".scss",
    ".java", ".c", ".cpp", ".h", ".hpp", ".cs", ".go", ".rs", ".php",
    ".rb", ".swift", ".kt", ".sql", ".sh", ".bat", ".ps1", ".env",
    ".ini", ".toml", ".log"
}


def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.2f} MB"


async def process_uploaded_file(filename: str, content: bytes, content_type: str = "") -> Dict[str, Any]:
    """
    Processes an uploaded file (image, PDF, Word document, code, or text file)
    and returns structured metadata, base64 data (for images), and extracted text.
    """
    ext = os.path.splitext(filename)[1].lower()
    size_bytes = len(content)
    size_human = format_size(size_bytes)

    # 1. Image Processing (Photos, Screenshots, Diagrams)
    if ext in IMAGE_EXTENSIONS or (content_type and content_type.startswith("image/")):
        try:
            img = Image.open(io.BytesIO(content))
            width, height = img.size
            img_format = (img.format or ext.replace(".", "") or "PNG").upper()

            # Resize if very large (> 2048px) to keep API payloads optimal while preserving detail
            max_dim = 2048
            if width > max_dim or height > max_dim:
                img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                save_fmt = "JPEG" if img_format in ("JPEG", "JPG") else "PNG"
                if save_fmt == "JPEG" and img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                img.save(buf, format=save_fmt, quality=88)
                processed_bytes = buf.getvalue()
                mime_type = f"image/{save_fmt.lower()}"
            else:
                processed_bytes = content
                mime_sub = "jpeg" if ext in (".jpg", ".jpeg") else ext.replace(".", "") or "png"
                mime_type = content_type if content_type.startswith("image/") else f"image/{mime_sub}"

            b64_data = base64.b64encode(processed_bytes).decode("utf-8")
            data_url = f"data:{mime_type};base64,{b64_data}"

            # Compute basic visual descriptors (dominant colors, orientation, brightness)
            small_rgb = img.convert("RGB").resize((32, 32))
            pixels = list(small_rgb.getdata())
            avg_r = sum(p[0] for p in pixels) // len(pixels)
            avg_g = sum(p[1] for p in pixels) // len(pixels)
            avg_b = sum(p[2] for p in pixels) // len(pixels)
            avg_hex = f"#{avg_r:02x}{avg_g:02x}{avg_b:02x}"
            orientation = "Paysage (horizontale)" if width > height else ("Portrait (verticale)" if height > width else "Carrée")

            return {
                "filename": filename,
                "category": "image",
                "mime_type": mime_type,
                "size": size_bytes,
                "size_human": size_human,
                "dimensions": f"{width}x{height}",
                "base64": b64_data,
                "data_url": data_url,
                "extracted_text": (
                    f"[Image jointe: {filename} | Format: {img_format} | Dimensions: {width}x{height}px ({orientation}) | "
                    f"Taille: {size_human} | Teinte moyenne dominante: RGB({avg_r}, {avg_g}, {avg_b}) / {avg_hex}]"
                ),
                "summary": f"Image {img_format} • {width}×{height} • {size_human}"
            }
        except Exception as e:
            return {
                "filename": filename,
                "category": "error",
                "error": f"Impossible de lire l'image: {str(e)}"
            }

    # 2. PDF Processing
    if ext in PDF_EXTENSIONS or content_type == "application/pdf":
        try:
            reader = PdfReader(io.BytesIO(content))
            num_pages = len(reader.pages)
            pages_text = []
            max_pages_to_extract = min(num_pages, 50)
            for idx in range(max_pages_to_extract):
                page_txt = reader.pages[idx].extract_text() or ""
                if page_txt.strip():
                    pages_text.append(f"--- Page {idx + 1} ---\n{page_txt.strip()}")

            full_text = "\n\n".join(pages_text)
            if len(full_text) > 60000:
                full_text = full_text[:60000] + "\n\n[... Contenu PDF tronqué à 60 000 caractères ...]"

            if not full_text.strip():
                full_text = "[Ce PDF ne contient pas de texte sélectionnable (document scanné ou images uniquement).]"

            return {
                "filename": filename,
                "category": "pdf",
                "mime_type": "application/pdf",
                "size": size_bytes,
                "size_human": size_human,
                "pages": num_pages,
                "extracted_text": full_text,
                "summary": f"Document PDF • {num_pages} page(s) • {size_human}"
            }
        except Exception as e:
            return {
                "filename": filename,
                "category": "error",
                "error": f"Erreur lors de la lecture du PDF: {str(e)}"
            }

    # 3. Word (.docx) Processing
    if ext in DOCX_EXTENSIONS or "wordprocessingml" in (content_type or ""):
        try:
            doc = docx.Document(io.BytesIO(content))
            paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
            # Extract tables as well
            for table in doc.tables:
                for row in table.rows:
                    row_cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                    if row_cells:
                        paragraphs.append(" | ".join(row_cells))

            full_text = "\n\n".join(paragraphs)
            if len(full_text) > 60000:
                full_text = full_text[:60000] + "\n\n[... Document Word tronqué à 60 000 caractères ...]"

            return {
                "filename": filename,
                "category": "document",
                "mime_type": content_type or "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "size": size_bytes,
                "size_human": size_human,
                "extracted_text": full_text or "[Document Word vide]",
                "summary": f"Document Word • {len(paragraphs)} paragraphes • {size_human}"
            }
        except Exception as e:
            return {
                "filename": filename,
                "category": "error",
                "error": f"Erreur lors de la lecture du fichier DOCX: {str(e)}"
            }

    # 4. Code, CSV, JSON, Markdown, and Text files
    try:
        try:
            decoded_text = content.decode("utf-8")
        except UnicodeDecodeError:
            decoded_text = content.decode("latin-1", errors="replace")

        lines_count = decoded_text.count("\n") + 1
        if len(decoded_text) > 60000:
            decoded_text = decoded_text[:60000] + "\n\n[... Fichier texte tronqué à 60 000 caractères ...]"

        is_code = ext in {
            ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".java",
            ".c", ".cpp", ".cs", ".go", ".rs", ".php", ".sql", ".sh", ".json", ".yaml", ".yml"
        }
        category = "code" if is_code else "text"
        label = "Fichier Code" if is_code else "Fichier Texte"

        return {
            "filename": filename,
            "category": category,
            "mime_type": content_type or "text/plain",
            "size": size_bytes,
            "size_human": size_human,
            "lines": lines_count,
            "extracted_text": decoded_text,
            "summary": f"{label} ({ext or 'txt'}) • {lines_count} lignes • {size_human}"
        }
    except Exception as e:
        return {
            "filename": filename,
            "category": "error",
            "error": f"Format de fichier non supporté ou illisible: {str(e)}"
        }
