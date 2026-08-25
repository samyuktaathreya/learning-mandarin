import json
import re
from pathlib import Path
import pdfplumber

# Dynamically set paths based on the script's location
SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPT_DIR.parent
RAW_DIR = BASE_DIR / "data" / "raw"
CLEAN_DIR = BASE_DIR / "data" / "clean"

def compile_hsk_pdfs():
    # Ensure the clean directory exists
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    output_json_path = CLEAN_DIR / "hsk_vocab_list.json"
    
    # Initialize the dictionary with string keys for levels 1-6
    vocab_dict = {str(i): {} for i in range(1, 7)}
    found_levels = set()
    
    # Regex for matching the vocabulary lines
    item_pattern = re.compile(
        r"^(\d+)\s+([\u4e00-\u9fa5\(\)\s]+)\s+([a-zA-Zāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ\(\)\s]+)\s+(.+)$"
    )

    # Grab all PDFs in the raw directory
    pdf_files = list(RAW_DIR.glob("*.pdf"))
    
    if not pdf_files:
        print(f"No PDFs found in {RAW_DIR}. Please add your files and try again.")
        return

    for pdf_path in pdf_files:
        # Extract the HSK level from the filename
        level_match = re.search(r'hsk\s*(\d)', pdf_path.name.lower())
        if not level_match:
            print(f"Skipping '{pdf_path.name}': Could not determine HSK level from filename.")
            continue
            
        hsk_level = int(level_match.group(1))
        if hsk_level not in range(1, 7):
            print(f"Skipping '{pdf_path.name}': Level {hsk_level} is out of bounds (1-6).")
            continue
            
        level_str = str(hsk_level)
        found_levels.add(hsk_level)
        current_category = "Uncategorized"
        
        print(f"Processing '{pdf_path.name}' (Level {hsk_level})...")
        
        try:
            with pdfplumber.open(pdf_path) as pdf:
                if len(pdf.pages) <= 1:
                    print(f"  Warning: '{pdf_path.name}' only has {len(pdf.pages)} page(s). Skipping.")
                    continue
                
                # Skip the first page (index 0)
                for page in pdf.pages[1:]:
                    text = page.extract_text()
                    if not text:
                        continue
                        
                    for line in text.split("\n"):
                        line = line.strip()
                        
                        # Skip table headers
                        if line.startswith("No. Chinese"):
                            continue
                            
                        # Identify category headers
                        if not re.match(r"^\d+", line) and not line.startswith("No."):
                            current_category = line
                            continue
                            
                        # Match vocabulary items
                        match = item_pattern.match(line)
                        if match:
                            item_id, chinese, pinyin, english = match.groups()
                            chinese_word = chinese.strip()
                            
                            # Nest the word directly under its HSK level
                            vocab_dict[level_str][chinese_word] = {
                                "id": int(item_id.strip()),
                                "category": current_category,
                                "pinyin": pinyin.strip(),
                                "english": english.strip()
                            }
        except Exception as e:
            print(f"  Error processing '{pdf_path.name}': {e}")
            continue

    # Remove any HSK levels from the dict that ended up with no words (missing PDFs)
    vocab_dict = {k: v for k, v in vocab_dict.items() if v}

    # Check for missing levels
    missing_levels = set(range(1, 7)) - found_levels
    if missing_levels:
        print(f"\nNote: Missing PDFs for HSK levels: {', '.join(map(str, sorted(missing_levels)))}")

    # Save to JSON
    if vocab_dict:
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(vocab_dict, f, ensure_ascii=False, indent=2)
            
        # Count total words across all levels
        total_words = sum(len(words) for words in vocab_dict.values())
        print(f"Successfully compiled {total_words} words to {output_json_path}")
    else:
        print("No vocabulary words were successfully extracted.")

if __name__ == "__main__":
    compile_hsk_pdfs()