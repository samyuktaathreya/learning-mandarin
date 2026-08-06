from hsk_sentences_audio import audio_url, iter_sentences, load_sentences

print(len(load_sentences()))
card = next(iter_sentences(level=2, topic="food"))
print(card["chinese"], audio_url(card, speed="slow"))

level_1_sentences = list(iter_sentences(level=1))
print(f"Found {len(level_1_sentences)} HSK 1 sentences.")
print(level_1_sentences[:1])