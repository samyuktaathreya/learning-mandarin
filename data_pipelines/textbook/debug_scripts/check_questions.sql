-- check_pipeline.sql -- health check for the textbook DB.
-- Run:  sqlite3 path/to/textbook.db < check_pipeline.sql
-- Every "should be 0" line must print 0. The other sections are for
-- reading over. Safe to run any time: it only reads.

.headers on
.mode column

-- =====================================================================
-- 1. OVERVIEW: what each stage produced
-- =====================================================================
SELECT '1a. vocab by HSK level (NULL = names / unlisted)' AS check_name;
SELECT hsk_level, COUNT(*) AS words, SUM(unit_id IS NOT NULL) AS with_unit
FROM vocab GROUP BY hsk_level ORDER BY hsk_level;

SELECT '1b. senses by origin' AS check_name;
SELECT origin, COUNT(*) AS senses, SUM(unit_id IS NOT NULL) AS with_unit
FROM vocab_senses GROUP BY origin;

SELECT '1c. sentences by level' AS check_name;
SELECT s.hsk_level,
       COUNT(*) AS sentences,
       SUM(EXISTS (SELECT 1 FROM sentence_vocab sv WHERE sv.sentence_id = s.id)) AS tagged,
       SUM(EXISTS (SELECT 1 FROM sentence_vocab sv WHERE sv.sentence_id = s.id)
           AND NOT EXISTS (SELECT 1 FROM sentence_vocab sv WHERE sv.sentence_id = s.id AND sv.vocab_sense_id IS NULL)) AS all_meanings_picked,
       SUM(EXISTS (SELECT 1 FROM sentence_grammar sg WHERE sg.sentence_id = s.id)) AS with_grammar,
       SUM(s.unit_id IS NOT NULL) AS with_unit
FROM sentences s GROUP BY s.hsk_level ORDER BY s.hsk_level;

SELECT '1d. units and questions by level' AS check_name;
SELECT u.hsk_level, COUNT(DISTINCT u.id) AS units,
       (SELECT COUNT(*) FROM questions q JOIN units u2 ON u2.id = q.unit_id WHERE u2.hsk_level = u.hsk_level) AS questions
FROM units u GROUP BY u.hsk_level;

-- =====================================================================
-- 2. CONSISTENCY: every line should be 0
-- (meanings are only picked for levels you build, so unbuilt levels' tags
-- having no sense is normal)
-- =====================================================================
SELECT '2. consistency checks (all should be 0)' AS check_name;
SELECT 'untagged sentences (tag_sentences)' AS should_be_0,
       COUNT(*) AS n FROM sentences s
       WHERE NOT EXISTS (SELECT 1 FROM sentence_vocab sv WHERE sv.sentence_id = s.id)
UNION ALL
SELECT 'tagged sentences with no level',
       COUNT(*) FROM sentences s
       WHERE s.hsk_level IS NULL AND EXISTS (SELECT 1 FROM sentence_vocab sv WHERE sv.sentence_id = s.id)
UNION ALL
SELECT 'built-level tags with no meaning picked (resolve_senses)',
       COUNT(*) FROM sentence_vocab sv JOIN sentences s ON s.id = sv.sentence_id
       WHERE sv.vocab_sense_id IS NULL AND s.hsk_level IN (SELECT DISTINCT hsk_level FROM units)
UNION ALL
SELECT 'sentences in a built level with no unit (assign_units)',
       COUNT(*) FROM sentences s
       WHERE s.unit_id IS NULL AND s.hsk_level IN (SELECT DISTINCT hsk_level FROM units)
UNION ALL
SELECT 'words used before their unit',
       COUNT(*) FROM sentence_vocab sv
       JOIN sentences s ON s.id = sv.sentence_id JOIN units su ON su.id = s.unit_id
       JOIN vocab v ON v.id = sv.vocab_id LEFT JOIN units vu ON vu.id = v.unit_id
       WHERE vu.id IS NULL OR (vu.hsk_level, vu.unit_number) > (su.hsk_level, su.unit_number)
UNION ALL
SELECT 'senses used before their unit',
       COUNT(*) FROM sentence_vocab sv
       JOIN sentences s ON s.id = sv.sentence_id JOIN units su ON su.id = s.unit_id
       JOIN vocab_senses vs ON vs.id = sv.vocab_sense_id LEFT JOIN units vu ON vu.id = vs.unit_id
       WHERE vu.id IS NULL OR (vu.hsk_level, vu.unit_number) > (su.hsk_level, su.unit_number)
UNION ALL
SELECT 'words with a unit but no sentence in that unit',
       COUNT(*) FROM vocab v WHERE v.unit_id IS NOT NULL AND NOT EXISTS (
         SELECT 1 FROM sentence_vocab sv JOIN sentences s ON s.id = sv.sentence_id
         WHERE sv.vocab_id = v.id AND s.unit_id = v.unit_id)
UNION ALL
SELECT 'taught senses with no display pinyin (finalize)',
       COUNT(*) FROM vocab_senses WHERE unit_id IS NOT NULL AND pinyin_display IS NULL
UNION ALL
SELECT 'tags with no display pinyin (finalize)',
       COUNT(*) FROM sentence_vocab sv JOIN sentences s ON s.id = sv.sentence_id
       WHERE s.unit_id IS NOT NULL AND sv.pinyin IS NULL
UNION ALL
SELECT 'words with senses but not exactly 1 primary (finalize)',
       COUNT(*) FROM (SELECT vocab_id FROM vocab_senses GROUP BY vocab_id HAVING SUM(is_primary) != 1)
UNION ALL
SELECT 'primary sense not the earliest-taught one',
       COUNT(*) FROM vocab_senses p JOIN units pu ON pu.id = p.unit_id
       WHERE p.is_primary = 1 AND EXISTS (
         SELECT 1 FROM vocab_senses o JOIN units ou ON ou.id = o.unit_id
         WHERE o.vocab_id = p.vocab_id AND (ou.hsk_level, ou.unit_number) < (pu.hsk_level, pu.unit_number))
UNION ALL
SELECT 'senses with UNKNOWN pinyin or english',
       COUNT(*) FROM vocab_senses WHERE pinyin GLOB '*UNKNOWN*' OR english GLOB '*UNKNOWN*'  -- GLOB is case-sensitive: 未知 means "unknown"
UNION ALL
SELECT 'units with no questions (create_questions)',
       COUNT(*) FROM units u WHERE NOT EXISTS (SELECT 1 FROM questions q WHERE q.unit_id = u.id);

-- =====================================================================
-- 3. UNITS: one row per unit (compare with assign_units' table)
-- =====================================================================
SELECT '3. units' AS check_name;
SELECT u.hsk_level AS lvl, u.unit_number AS n, u.title,
       (SELECT COUNT(*) FROM sentences s WHERE s.unit_id = u.id) AS sentences,
       (SELECT COUNT(*) FROM vocab v WHERE v.unit_id = u.id) AS new_words,
       (SELECT COUNT(*) FROM grammar_tips g WHERE g.unit_id = u.id) AS new_grammar,
       (SELECT COUNT(*) FROM questions q WHERE q.unit_id = u.id) AS questions
FROM units u ORDER BY u.hsk_level, u.unit_number;

-- =====================================================================
-- 4. REVIEW LISTS: read these over
-- =====================================================================
SELECT '4a. AI-added meanings (no dictionary meaning fit) -- review' AS check_name;
SELECT v.hanzi, vs.pinyin, vs.english, u.hsk_level AS lvl, u.unit_number AS unit
FROM vocab_senses vs JOIN vocab v ON v.id = vs.vocab_id LEFT JOIN units u ON u.id = vs.unit_id
WHERE vs.origin = 'ai_added' ORDER BY lvl, unit;

SELECT '4b. words outside the HSK list (names and unlisted)' AS check_name;
SELECT v.hanzi, v.word_type, v.english, u.hsk_level AS lvl, u.unit_number AS unit
FROM vocab v LEFT JOIN units u ON u.id = v.unit_id
WHERE v.origin IN ('proper_noun', 'unlisted') ORDER BY lvl, unit;

SELECT '4c. words with the most senses (check they are real distinct meanings)' AS check_name;
SELECT v.hanzi, COUNT(*) AS senses, GROUP_CONCAT(vs.english, '  |  ') AS meanings
FROM vocab_senses vs JOIN vocab v ON v.id = vs.vocab_id
GROUP BY v.id ORDER BY senses DESC LIMIT 15;

SELECT '4d. grammar points per level: found in how many sentences' AS check_name;
SELECT g.hsk_level AS lvl, COUNT(*) AS points,
       SUM(n >= 1) AS found, SUM(n >= 20) AS found_20_plus, SUM(n = 0) AS never_found
FROM (SELECT g.id, g.hsk_level,
             (SELECT COUNT(*) FROM sentence_grammar sg WHERE sg.grammar_tip_id = g.id) AS n
      FROM grammar_tips g) g
GROUP BY g.hsk_level;

SELECT '4e. HSK 1 grammar points found in fewest sentences' AS check_name;
SELECT g.grammar_code, g.raw_text, COUNT(sg.sentence_id) AS sentences
FROM grammar_tips g LEFT JOIN sentence_grammar sg ON sg.grammar_tip_id = g.id
WHERE g.hsk_level = 1 GROUP BY g.id ORDER BY sentences LIMIT 12;

SELECT '4f. five random tagged sentences, word by word' AS check_name;
SELECT s.hanzi, s.pinyin, u.title AS unit,
       GROUP_CONCAT(v.hanzi || ' ' || COALESCE(sv.pinyin, '?') || ' = ' || COALESCE(vs.english, '?'), '  |  ') AS words
FROM (SELECT * FROM sentences WHERE unit_id IS NOT NULL ORDER BY RANDOM() LIMIT 5) s
JOIN sentence_vocab sv ON sv.sentence_id = s.id JOIN vocab v ON v.id = sv.vocab_id
LEFT JOIN vocab_senses vs ON vs.id = sv.vocab_sense_id LEFT JOIN units u ON u.id = s.unit_id
GROUP BY s.id;

SELECT '4g. five random fill-in-the-blank questions' AS check_name;
SELECT u.hsk_level AS lvl, u.unit_number AS unit, q.question, q.answer
FROM questions q JOIN units u ON u.id = q.unit_id
WHERE q.question_type = 'fill in the blank' ORDER BY RANDOM() LIMIT 5;

SELECT '4h. questions by type' AS check_name;
SELECT question_type, COUNT(*) AS n FROM questions GROUP BY question_type ORDER BY n DESC;