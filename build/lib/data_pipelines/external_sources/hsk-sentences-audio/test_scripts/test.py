from hsk_sentences_audio import audio_url, iter_sentences, load_sentences

print(len(load_sentences()))
card = next(iter_sentences(level=2, topic="food"))
print(card["tokens"])
print("chinese: ")
print(card["chinese"])