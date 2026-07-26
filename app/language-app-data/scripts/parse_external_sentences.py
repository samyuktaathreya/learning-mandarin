import csv
import json
import re
from pathlib import Path

# --- File Paths ---
# Assuming script runs from language-app-data/scripts/
BASE_DIR = Path(__file__).parent.parent
VOCAB_PATH = BASE_DIR / "data" / "clean" / "unit_vocab_tags.json"
CSV_PATH = BASE_DIR / "data" / "raw" / "extracted-500-sentences-mandarin.csv"
OUTPUT_PATH = BASE_DIR / "data" / "clean" / "external_mandarin_sentences.json"
UNITS_OUTPUT_PATH = BASE_DIR / "data" / "clean" / "units_output.json"

# --- Punctuation to Ignore ---
CHINESE_PUNCTUATION = set("！？｡。＂＃＄％＆＇（）＊＋，－／：；＜＝＞＠［＼］＾＿｀｛｜｝～｟｠｢｣､、〃》「」『』【】〔〕〖〗〘〙〚〛〜〝〞〟〰〾〿–—‘’‛“”„‟…‧﹏.?!")

def load_vocab():
    """Loads vocab and maps each word to the integer unit it was introduced in."""
    with open(VOCAB_PATH, 'r', encoding='utf-8') as f:
        vocab_json = json.load(f)
        
    word_to_unit = {}
    for unit_str, words in vocab_json.items():
        unit_num = int(unit_str)
        for word in words:
            if word not in word_to_unit or unit_num < word_to_unit[word]:
                word_to_unit[word] = unit_num
    return word_to_unit

def convert_pinyin_to_numbers(pinyin_str):
    """Converts pinyin with tone marks to pinyin with tone numbers, stripping punctuation."""
    tone_map = {
        'ā': ('a', 1), 'á': ('a', 2), 'ǎ': ('a', 3), 'à': ('a', 4),
        'ē': ('e', 1), 'é': ('e', 2), 'ě': ('e', 3), 'è': ('e', 4),
        'ī': ('i', 1), 'í': ('i', 2), 'ǐ': ('i', 3), 'ì': ('i', 4),
        'ō': ('o', 1), 'ó': ('o', 2), 'ǒ': ('o', 3), 'ò': ('o', 4),
        'ū': ('u', 1), 'ú': ('u', 2), 'ǔ': ('u', 3), 'ù': ('u', 4),
        'ǖ': ('v', 1), 'ǘ': ('v', 2), 'ǚ': ('v', 3), 'ǜ': ('v', 4),
        'ü': ('v', 5)
    }
    
    words = pinyin_str.split()
    numbered_words = []
    
    for word in words:
        clean_word = re.sub(r'[^\wāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜü]', '', word, flags=re.UNICODE)
        if not clean_word:
            continue
            
        tone = 5
        base_word = ""
        for char in clean_word:
            if char in tone_map:
                base_char, t = tone_map[char]
                base_word += base_char
                tone = t
            else:
                base_word += char
        numbered_words.append(f"{base_word}{tone}")
        
    return " ".join(numbered_words)

def segment_sentence(hanzi_str, vocab_set):
    """
    Uses DP/Backtracking with memoization to find a valid segmentation of the sentence 
    using only words in the vocab_set. Returns a list of words or None if impossible.
    """
    memo = {}
    
    def backtrack(s):
        if not s:
            return []
        if s in memo:
            return memo[s]
        
        for i in range(len(s), 0, -1):
            prefix = s[:i]
            if prefix in vocab_set:
                remainder = backtrack(s[i:])
                if remainder is not None:
                    memo[s] = [prefix] + remainder
                    return memo[s]
                    
        memo[s] = None
        return None

    return backtrack(hanzi_str)

def merge_into_units_output(new_sentences_data):
    """
    Takes the newly generated dictionary of sentences (grouped by unit) 
    and merges it into the existing units_output.json file.
    """
    # Load existing units_output.json if it exists
    if UNITS_OUTPUT_PATH.exists():
        with open(UNITS_OUTPUT_PATH, 'r', encoding='utf-8') as f:
            existing_data = json.load(f)
    else:
        existing_data = {}

    for unit_str, new_unit_data in new_sentences_data.items():
        new_sentences = new_unit_data.get("sentences", [])
        
        # If the unit doesn't exist in the target JSON, create a scaffold for it
        if unit_str not in existing_data:
            existing_data[unit_str] = {
                "sentences": [],
                "fill_in_the_blank": [],
                "counts": {
                    "merged": {
                        "sentences_final": 0,
                        "fitb_questions_final": 0
                    }
                }
            }

        # Extend the existing sentences array with the new ones
        existing_data[unit_str]["sentences"].extend(new_sentences)
        
        # Update the final sentence count to keep data accurate
        try:
            current_count = existing_data[unit_str]["counts"]["merged"]["sentences_final"]
            existing_data[unit_str]["counts"]["merged"]["sentences_final"] = current_count + len(new_sentences)
        except KeyError:
            # Failsafe if the existing JSON is missing the nested "counts" keys
            pass

    # Save the merged data back to units_output.json
    with open(UNITS_OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(existing_data, f, ensure_ascii=False, indent=2)
        
    print(f"Successfully merged new sentences into {UNITS_OUTPUT_PATH}")


def main():
    word_to_unit = load_vocab()
    vocab_set = set(word_to_unit.keys())
    
    output_data = {}

    with open(CSV_PATH, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        for row in reader:
            # Use 5 to match your 5-column CSV format
            if not row or len(row) < 5:
                continue
                
            # Correctly map the CSV columns
            sentence_id = row[0]
            vocab_word = row[1]
            pinyin_marks = row[2]
            hanzi = row[3]
            english = row[4] 
            
            # Strip punctuation for segmentation checking
            clean_hanzi = "".join(char for char in hanzi if char not in CHINESE_PUNCTUATION)
            
            tags = segment_sentence(clean_hanzi, vocab_set)
            
            # If tags is None, it means the sentence contains unknown characters/words
            if tags is None:
                continue
                
            # Sentence unit is the max unit among all its constituent words
            sentence_unit = max(word_to_unit[word] for word in tags)
            unit_str = str(sentence_unit)
            
            if unit_str not in output_data:
                output_data[unit_str] = {"sentences": []}
                
            output_data[unit_str]["sentences"].append({
                "hanzi": hanzi,
                "english": english,
                "tags": tags,
                "pinyin": convert_pinyin_to_numbers(pinyin_marks)
            })

    # Ensure output directory exists
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    # 1. Save the standalone new sentences
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    print(f"Successfully processed sentences and saved to {OUTPUT_PATH}")

    # 2. Merge them into the main units file
    merge_into_units_output(output_data)

if __name__ == "__main__":
    main()