from hsk_sentences_audio import iter_sentences

found_matches = []

# Iterate through all Level 1 sentences
for card in iter_sentences(level=1):
    tokens = card.get("tokens", [])
    
    # Check if any token's 'word' attribute is exactly '不是'
    for token in tokens:
        if token.get("word") == "不是":
            found_matches.append({
                "sentence_id": card.get("id"),
                "chinese": card.get("chinese"),
                "token": token
            })
            break  # Move to next card once a match is found

# Output results
if found_matches:
    print(f"Found {len(found_matches)} sentence(s) where '不是' is a single token:\n")
    for item in found_matches:
        print(f"ID: {item['sentence_id']}")
        print(f"Chinese: {item['chinese']}")
        print(f"Token: {item['token']}\n")
else:
    print("No tokens with '不是' were found in HSK Level 1 sentences.")