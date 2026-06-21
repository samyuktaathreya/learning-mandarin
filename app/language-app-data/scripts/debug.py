import fitz  # PyMuPDF

PDF_PATH = "../data/raw/hsk1_textbook.pdf"  # update to match your actual path

try:
    doc = fitz.open(PDF_PATH)
except Exception as e:
    raise SystemExit(f"Failed to open PDF: {e}")

if doc.is_closed:
    raise SystemExit("Document opened but is closed/invalid.")

if doc.page_count == 0:
    raise SystemExit("Document opened but has 0 pages -- likely corrupted or not a real PDF.")

print(f"Loaded successfully: {PDF_PATH}")
print(f"Page count: {doc.page_count}")
print(f"Is encrypted: {doc.is_encrypted}")

# Quick sanity check: does page 1 actually have extractable text?
sample_text = doc[0].get_text("text").strip()
if not sample_text:
    print("WARNING: page 1 has no extractable text -- may be a scanned/image-only PDF.")
else:
    print(f"Sample text from page 1: {sample_text[:200]!r}")

doc.close()