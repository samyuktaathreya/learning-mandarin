# Grammar detection rules (trial)

Written by AI (try_grammar_rule.py write), checked against the pipeline's sentences (try_grammar_rule.py validate), and safe to edit by hand. Only the ```rule block of each section is read by the code. Syntax: see test_scripts/grammar_rules.py.

## 1-10
<!-- written by claude-sonnet-5-5 on 2026-11-06; edit freely -->
**Point:** The scope adverb 都 ("all / both") and the togetherness adverbs 一起 / 一块儿 ("together"), placed after the subject and before the verb.

**Covers:** A plural, coordinated or collective subject followed by 都 and a predicate (他们都在上海, 我和我太太都不吃肉, 同学们都很认真). Also 一起 or 一块儿 directly before a verb (我们一起吃晚饭吧, 他想和他太太一起学中文).

**Does not cover:** Every other use of 都, because each is a different grammar point.
- 连…都 ("even"), 一+量词+都+不/没 ("not even one"), and question words with 都 (什么/谁/哪儿/怎么/多少…都).
- 除了…都, 不管/无论…都, 别说, and 何况/更 ("let alone").
- 都…了 ("already") and the "even" use 都能….
- Sentence-initial 都 (都怪你).

```rule
match: /PN|NN|NR|NT|M|CD 都/AD
match: 一起/AD
match: 一块儿/AD
unless: 连
unless: 什么
unless: 谁
unless: 哪儿
unless: 哪里
unless: 哪
unless: 怎么
unless: 多少
unless: 除了
unless: 不管
unless: 不 管
unless: 无论
unless: 别说
unless: 何况
unless: 更
unless: 一/CD ... 都
unless: 都 ... 了
unless: 都 能
```

**Notes:**
- The first `match:` line requires a nominal or measure word right before 都. This rejects sentence-initial 都, 去都去了 and 遍地都是.
- The `unless:` lines are checked against the whole sentence, not one clause. A bare word such as 除了 or 连 therefore excludes the sentence even when it sits in a different clause from 都.
- `都 ... 了` also removes some valid sentences, such as 笔试和口试都通过了. This is deliberate: it keeps out the "already" use at the cost of some recall.
- 一起 and 一块儿 sentences are also rejected by any of the `unless:` words. That is acceptable under the precision-first policy.

## 1-42
<!-- written by claude-sonnet-5-5 on 2026-11-07; edit freely -->
**Point:** The progressive aspect. 在 / 正在 / 正 before a verb, or 呢 and 着 at the end, shows an action in progress (她在看书, 外边正在下雨, 我吃饭呢).

**Covers:**
- 在, 正在 or 正 tagged as an adverb (AD) directly before a verb, e.g. 她在看书, 我们正在上课, 他正上厕所呢, 我正吃着饭呢.
- The tagger's occasional P-tag variant, but only for the fixed phrase 在上厕所.
- Subject + verb + noun + 呢 with no 在 or 正, limited to common activity verbs (吃, 看, 上, 开, 打 …), e.g. 我吃饭呢, 爷爷看报纸呢.
- Subject + verb + 着 + noun + 呢, e.g. 老板开着会呢, 我们上着汉语课呢.

**Does not cover:**
- 在 as a location preposition or verb (在上海上大学, 坐在…, 在床上吃东西, 他不在学校).
- 早在, 在于, 在…内, 包括…在内, and 在…下/上.
- 正好, and 正 + 是 (现在正是…).
- 正 before a modal or intention verb (正要, 正想), which means "just about to" rather than "in progress".
- 在乎.

**Rule:** the AD tag separates progressive 在 from preposition 在 (P) and verb 在 (VV).

```rule
match: 在|正在|正/AD /VV
match: 在/P 上/VV 厕所
match: ^ /NN|PN 吃|看|上|开|打|做|写|听|玩|洗|喝|学|睡|说|画|读/VV /NN 呢/SP $
match: ^ /NN|PN /VV 着/AS /NN /NN? 呢/SP $
unless: 早 在
unless: 正|正在/AD 要|想|会|能|可以|准备|打算|应该|得|需要|愿意/VV
unless: 在/AD 在|于|乎
```

**Notes:**
- Known misses:
  - 谁在里面洗澡 (在/P + place + verb) is not matched, because it looks the same as the location-before-verb pattern (病人在医院休息), which must stay excluded.
  - Progressive 在 that the tagger labels P is also missed, except the 在上厕所 case.
- The 呢 patterns are deliberately narrow: a closed verb list, a bare NN or PN subject, and a clause-final 呢. Widening them risks matching 喜欢苹果呢 and similar sentences.
- The 着…呢 pattern requires a noun object between 着 and 呢, which avoids existential sentences such as 墙上挂着画 and stative ones like 门开着呢.

## 1-13
<!-- written by claude-sonnet-5-5 on 2026-11-07; edit freely -->
**Point:** The adverbs 也 ("also/too", a second subject doing the same thing) and 还 ("and also", one subject doing an additional thing).
**Covers:** 也 placed before a verb, adjective, 是, 有 or an auxiliary, e.g. 我也喜欢, 他也很胖, 他们也会去吗, 我也不知道. Also 还 at the start of a clause, right after a comma, when it adds a further action or object, e.g. 他要去上海，还要去北京 and 你要一杯咖啡，还要什么.
**Does not cover:** Fixed patterns that use the same words, each excluded by an `unless:` line:
- 哪怕/就算/即使/就是/宁可/不管/无论/虽然/尽管 … 也
- 连 … 也, 一点/一+量词 … 也, 再 … 也, 再也
- 什么/谁/怎么 … 也
- 除了, 既, 不但/不仅/不光/而而且 patterns
- 还是, 还好, and 还 + 不/没, since 还 is followed by an adverb there rather than a verb
- 还 + 在/行/可以/好, and 还 … 呢 or 还 … 吗

Also excluded: 也 left hanging at the end of a clause.
**Notes:** The patterns cannot look across clauses, so the rule cannot check that a 还-clause has a preceding clause. A sentence-initial 还有… (e.g. 还有一个人没来) will therefore match, which is borderline. Every `unless:` is checked against the whole sentence, so it is deliberately aggressive, in line with precision first. 也 is matched whether the tagger gives it AD or CC. 还 is matched as AD or CC, but only as the first word of a clause and directly before a VV or VE.

```rule
match: 也 _
match: ^ 还/AD /VV|VE
match: ^ 还/CC /VV|VE
unless: 哪怕
unless: 就算
unless: 即使
unless: 即便
unless: 就/AD 算/VV
unless: 就是
unless: 就/AD 是/VC
unless: 宁可
unless: 宁愿
unless: 不管
unless: 无论
unless: 不论
unless: 虽然
unless: 尽管
unless: 别说
unless: 连
unless: 甚至
unless: 一点
unless: 一点儿
unless: 一/CD ... 也
unless: 再也
unless: 再 也
unless: 再 ... 也
unless: 什么 ... 也
unless: 谁 ... 也
unless: 怎么 ... 也
unless: 哪 ... 也
unless: 除了
unless: 除
unless: 既
unless: 不但
unless: 不仅
unless: 不光
unless: 而且
unless: 还是
unless: 还好
unless: ^ 还 在
unless: ^ 还 行
unless: ^ 还 可以
unless: ^ 还 不错
unless: ^ 还 ... 呢
unless: ^ 还 ... 吗
unless: 也好
unless: 也许
```

## 1-18
<!-- written by claude-sonnet-5-5 on 2026-11-07; edit freely -->
**Point:** Basic 比 comparison: A + 比 + B + adjective, optionally with 更 before the adjective.
**Covers:** Sentences where 比 is tagged as a preposition (P), followed by the thing compared and then an adjective (VA) in the same clause. This includes 更 + adjective, the question form with 吗, and a complement after the adjective such as 高一级 or 便宜得多. It also covers the case where 更 好 is tagged AD before a verb-tagged adjective, as in 比汉字更好玩.
**Does not cover:** 比较 used as "rather". 跟/和/与…比 (起来) and 相比, because the tagger marks 比 there as VV. 比起…. 比赛, 比如 and 对比. Score expressions such as 三比一. 比 with a verb after 更, such as 我比他更喜欢…, which is excluded as borderline.

```rule
match: 比/P ... /VA
match: 比/P ... 更/AD 好/AD
```

**Notes:** The rule relies on the tagger marking true comparison 比 as P and all the other uses as VV or part of a longer word (比较, 比赛, 比如 are single tokens). If the tagger starts tagging 比起 or 跟…比 as P, add an `unless:` for those patterns. A clause-initial 比, as in 我坐高铁去，比大巴快, still matches because patterns are clause-based.

## 2-32
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** The aspect particle 过 after a verb says the subject has (or has never) had an experience, as in 去过中国 and 没学过这个词.

**Covers:** A verb followed by 过 as an aspect particle, in affirmative, negative (没), or question (吗) sentences, with or without an object. Examples: 我也吃过日本菜, 我们都没坐过飞机, 你学过中文吗. It also covers 见过, which the tagger sometimes tags 见/VV 过/VV.

**Does not cover:**
- 经过, 通过, 不过, 莫过于, 超过, 过于, 过年, 过去 and 过来. These are single words or other structures, so a bare 过 never matches inside them.
- Direction-complement uses such as 走过来 and 搬过去.
- Motion-past uses such as 走过这条街, which describe passing through a place, not an experience. Verbs of this kind (走, 跑, 飞, 游, 跳) are excluded.
- Completion uses where 过 is followed by 了, such as 吃过饭了. These are borderline, so they are excluded.
- Sentences that also contain the contrast word 不过, such as 这家餐厅我没去过，不过我听说还不错. These are labeled as examples of 不过, not of this point.

```rule
match: /VV 过/AS
match: 见 过/VV
match: 吃|喝|看|听|学|去|来|买|写|读|玩|用|住|做|坐|想|说|到 过 /NN|NR|DT|CD|PN|M|OD
unless: 走|跑|飞|游|跳 过
unless: 过/AS ... 了
unless: 过/AS 了
unless: 过 ... 了
unless: 过 了
unless: 过 日子|年|节|生日
unless: 不过
```

**Notes:**
- The main pattern needs a VV directly before an AS-tagged 过. The only VV-tagged 过 it allows is the one after 见, because the tagger gets that example wrong. Allowing 过/VV after other verbs would let in 走过来 and 过上 (as in 过上好日子).
- Some sentences have 过 tagged ASP, which the rule syntax cannot reference, as in 我也吃过日本菜. The third match line covers these with an untagged 过. It is limited to a list of common experience verbs followed by a nominal element, to keep out 过来, 过上 and 过日子.
- The unless lines are deliberately conservative to favor precision. Some real experience sentences, such as 我走过这条路, are lost.
- Unless lines are checked over the whole sentence, so any 了 after 过, or any 不过, blocks the match.

## 3-41
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** 一 + measure word (+ noun) + 也/都 + 不/没 + verb, which emphasizes "not even one" (一个人都没有, 一口也没吃).
**Covers:** Clauses with 一 (CD) directly followed by a measure word, an optional noun (or two nouns, or JJ + noun), then 也 or 都 (AD), then 不 or 没 (AD) or 没有 (VE/AD). The noun may be absent (我一口都没吃). Topicalized objects before the subject are also fine, because the pattern only looks at the 一…也/都…不/没 stretch.
**Does not cover:** 一 + MW with no negation, such as 他得了一个大奖. Also excluded: 什么/谁/哪儿 + 都, 再也不/没, 连…都, 都 meaning "already" or "all" without 一 + MW, and a bare 也/都 + 没有 with no 一 + MW before it. These are other points.

```rule
match: 一/CD /M 也|都/AD 不|没/AD
match: 一/CD /M 也|都/AD 没有|没/VE
match: 一/CD /M 也|都/AD 没有/AD
match: 一/CD /M /NN 也|都/AD 不|没/AD
match: 一/CD /M /NN 也|都/AD 没有|没/VE
match: 一/CD /M /NN 也|都/AD 没有/AD
match: 一/CD /M /NN /NN 也|都/AD 不|没/AD
match: 一/CD /M /NN /NN 也|都/AD 没有|没/VE
match: 一/CD /M /NN /NN 也|都/AD 没有/AD
match: 一/CD /M /JJ /NN 也|都/AD 不|没/AD
match: 一/CD /M /JJ /NN 也|都/AD 没有|没/VE
match: 一/CD /M /JJ /NN 也|都/AD 没有/AD
unless: 连 ...
unless: 再 也/AD
unless: 何况
unless: 不管
unless: 无论
```

**Notes:** The earlier version wrote `_/M` and `_/NN`, which this pattern syntax does not accept as "any word with this tag", so none of the lines could match. Tag-only items are now written as `/M`, `/NN` and `/JJ`. Each match line requires 一 (CD), then a measure word, then optionally one or two nouns or a JJ + NN, and then 也/都 followed immediately by a negator, so ordinary 一 + MW + noun objects don't fire. Negators tagged 没有/VE or 没有/AD (the tagger sometimes mislabels it) are handled in separate lines. The unless lines drop 连…都 (a different point), 再也, 何况 and 不管/无论 sentences, which are borderline. The ordinary 一 + MW sequences (一个人, 一次) cannot match without 也/都 plus a negator after them. If 一点 is tagged M it is covered (一点消息都不知道). If a tagger gives 一点 as AD or something else, it will be missed, which is acceptable.

## 1-01
<!-- written by claude-sonnet-5-5 on 2026-11-07; edit freely -->
**Point:** 在 + place + a direction or location word (上, 里, 下, 旁边, 外面, etc.) says where something is. A bare 在 + place name (在中国) also counts.
**Covers:** 
- 在 + noun (with up to two words between) + 上/里 tagged LC: 书在桌子上, 手机在书包里, 在会议上, 发票在我的口袋里.
- 在 + noun + 下 tagged NN, as in 他在楼下.
- 在 + noun + 旁边, as in 在我家旁边.
- 在 + 外面/里面/上面/下面/前面/后面/旁边 tagged NN, standing alone as the end of the clause (我在外面).
- A whole clause that is just 在 + a place name (在中国, 在上海, 在纽约).

**Does not cover:** 
- 以上/以下/以外/以前/以后/之前/之后 limit and time words.
- 一下 and 下去 (verb + 下去).
- The idiom 在…下 with 下 tagged LC ("under such conditions").
- 然后/后来 sequencing.
- 在 + place + a verb phrase, as in 他在英国读完了大学. Place names are accepted only when they are the whole clause.

```rule
match: 在/VV|P /NN 上|里/LC
match: 在/VV|P _ /NN 上|里/LC
match: 在/VV|P _ _ /NN 上|里/LC
match: 在/VV /NN 下/NN
match: 在/VV|P _ 旁边/NN
match: 在/VV|P _ _ 旁边/NN
match: ^ 在/VV|P 外面|里面|上面|下面|前面|后面|旁边|外边|里边|上边|下边|前边|后边/NN $
match: ^ 在/VV|P /NR $
match: ^ 在/VV|P /NR /NR $
match: ^ 在/P /NN $
unless: 以|之 /P|LC|NN|NT 上|下|外|内|前|后
unless: 一 下/M|AD|VV
unless: 下去/VV
```

**Notes:** 
- The standalone place-name patterns are anchored with ^ and $ on purpose. Without the anchors they would also match 在 + place + a verb phrase, such as 他在英国读完了大学, which the counter-examples exclude.
- 下 is accepted only when tagged NN (楼下). The idiom 在…条件下 tags 下 as LC, so it is not matched.
- The middle `_` slots allow possessives (我的口袋里) and measure phrases. If false positives show up, restrict them to specific tags.
- The `unless` lines are mostly a safeguard. The match patterns already do not fire on the listed counter-examples.

## 1-02
<!-- written by claude-sonnet-5-5 on 2026-11-07; edit freely -->
**Point:** The modal verbs 会 and 能 before a verb phrase: 会 for a learned skill or "will", 能 for ability, possibility or a polite request.

**Covers:** 会 or 能 (also 不会, 不能, 很能, 太能) directly before a verb, adjective, preposition or adverb. For example 他会说中文, 明天会下雨吗, 你能帮我吗, 他很能说. It also covers 可不可以 and 可以 + verb, because the labeled examples include them.

**Does not cover:**
- 总会/总能/总不会/总不能 (the 总 construction) and 将会 (the 将 construction).
- 会 as a noun (开会, 国会, 会议, 机会, 误会).
- 能 inside nouns (能力, 功能, 能量) and 才能 as one token.
- Resultative 学会/听会/看会/懂会/教会, and 会/能 with no following verb phrase (e.g. "我不会。").

```rule
match: 会|能 /VV|VA|P|AD
match: 可 不 可以
match: 可以 /VV
unless: 总 ... 会|能
unless: 将 ... 会|能
unless: 会|能/NN
unless: 学|学习|听|看|懂|教 会
```

**Notes:**
- I avoided the tag MD, which is not in the allowed list. Words are matched untagged, and the following-word condition keeps out the noun uses and the bare "会/能" cases.
- 不会/MD and 可以/MD are still caught because the match depends on the next word's tag, not on 会's own tag.
- The 总 and 将 exclusions apply to the whole clause, so a clause with both 总 and a later 会 or 能 is dropped. That favors precision.
- 可以 + verb is included only because the labeled examples require it. Remove the last two match lines if 可以 should belong to a different point.
- A few real 会-as-"meet" or resultative uses may slip through when the tokenizer splits them.

## 1-03
<!-- written by claude-sonnet-5-5 on 2026-11-07; edit freely -->
**Point:** The modal verbs 想 and 要 used for "want (to)". This includes 想要, 要 + noun ("I want a cup of water"), and 想 + noun meaning "miss".

**Covers:**
- 要 or 想 directly governing a verb phrase, as in 我想学中文 and 他要去书店. This includes 要 followed by an adverb, preposition or adjective before the verb, as in 我要跟你一起去 and 你要早点睡觉.
- 要 after a noun, pronoun, time word or measure word subject, as in 爸爸要买一个新手机 and 老板下周要出差吗.
- 要 at the start of a clause when the subject is dropped, as in 要好好休息.
- 要 + noun for ordering or wanting something, as in 我们要三碗米饭 and 你们都要冰可乐吗.
- 想要.
- The 要不要 question form.
- 想 + noun meaning "miss", as in 我想你.
- Negatives such as 不想, and 什么都不要.
- 要 with a time word as a plan, as in 星期五我们要开会.

**Does not cover:**
- 要 in other patterns: 要是, 要不是, 要不, 要么, 要……就……, 非要……不可, 就要/快要/将要……了, and 要看 ("it depends"). Each of these is a different grammar point.
- 不要 as a prohibition, since it is tagged AD.
- Sentence-initial 要我说.
- "About to happen" 要……了, with or without a noun subject, as in 圣诞节要到了 and 我要生气了. It is a separate point.
- Conditional 要再说 ("if you say more"), as in 你要再说，我真生气了.

```rule
match: 想/VV _
match: /PN|NN|NT|NR|M 要/VV /VV|NN|PN|CD|DT|NR|M|JJ|VA|AD|P
match: /PN|NN|NT|NR|M 也|都|还|先|再 要/VV /VV|NN|PN|CD|DT|NR|M|JJ|VA|AD|P
match: ^ 要/VV /VV|NN|PN|CD|DT|NR|M|JJ|VA|AD|P
match: 要/VV 不/AD 要/VV
match: 什么 都|也 不要/VV
unless: 要是|如果/CS
unless: 要是
unless: 要/VV 不/AD 是/VC
unless: 要不是|要不|要么
unless: 非 ... 要
unless: 就|快|将 要/VV
unless: 要/VV ... 就/AD
unless: 要/VV 看|得看
unless: 要/VV ... 了/SP
unless: 要/VV 再说
unless: 要/VV 我/PN 说
```

**Notes:**
- The old `unless: /NN 要/VV ...` blocked every noun subject, such as 爸爸要买 and 宝宝要睡觉. I removed it. Time-word subjects with "about to" 要……了 (圣诞节要到了) are now blocked by `要/VV ... 了/SP` instead.
- The same `要 ... 了` exclusion removes 我要生气了.
- 要不是 is tokenized as 要/VV 不/AD 是/VC, so the old single-word unless never fired. It is now written as a sequence, and 要不要 is not affected.
- 你要再说，我真生气了 is a conditional, so it is blocked by `要/VV 再说`. This is narrow on purpose, to avoid blocking ordinary 要再 + verb sentences.

## 1-04
<!-- written by claude-sonnet-5-5 on 2026-11-07; edit freely -->
**Point:** Interrogative pronouns used to ask questions: 怎么 / 怎么样, 几, 多少, 多 + adjective, 谁, 什么, 哪 / 哪儿 / 哪里 / 哪些. The point also covers 几 meaning "several" and the money forms 多少钱 and 块…毛.

**Covers:**
- 怎么 before a verb or adjective, meaning "how" or "why", including 怎么这么/那么 + adj.
- 怎么样 as a predicate (tagged VV or VA).
- 几 + measure word or number (几个, 几十, 几百家, 好几次, 几点).
- 多少 as a real question.
- 多大 / 多高 / 多长 / 多远 at the end of a clause.
- 是谁 and a clause-initial 谁.
- A clause-final 什么 after a verb or 是.
- 哪 + measure word (哪个, 哪位).
- Clause-final 哪里 / 哪儿.
- Single-word 怎么 / 为什么 / 什么, and a bare 什么时候.
- Money amounts of the form 块 + number + 毛, and a bare "number + 块".

**Does not cover:**
- 不怎么 / 没怎么, which is a different point.
- 怎么 / 多少 / 谁 / 哪 / 多大 followed by 都 or 也, which is the "every / any" use.
- 什么的 and X什么X challenges.
- Indefinite "some" uses such as 点什么 or 在哪儿见过.
- 没什么, which means "nothing".
- 好/VA + 几, because the tagger mis-tags it in sentences from other points.

```rule
match: 怎么/AD
match: 怎么样/VV|VA
match: 几/CD /M
match: 几/M /M
match: 几/CD /CD
match: 好/AD 几/CD
match: 多少/CD
match: 多/AD 大|高|长|远/VA /SP? $
match: 是/VC 谁/PN
match: ^ 谁/PN
match: 哪/DT /M
match: 哪些
match: 哪里|哪儿/PN $
match: /VV 什么/PN $
match: 是/VC 什么/PN $
match: 块|元/M /CD 毛|分|角/M
match: ^ /CD 块|元/M $
match: ^ 怎么|为什么|什么 $
match: ^ 什么 时候 $
unless: 不 怎么
unless: 没 怎么
unless: 不 怎么样
unless: 怎么 ... 都|也/AD
unless: 多少 ... 都|也/AD
unless: 多 ... 都|也/AD
unless: 谁 ... 都|也/AD
unless: 哪 ... 都|也/AD
unless: 哪儿|哪里 ... 都|也/AD
unless: 什么 的
unless: 什么 ... 都|也/AD
unless: 点|点儿|些 ... 什么
unless: 没 什么
unless: 好/VA 几
unless: 什么 _ 什么
```

**Notes:**
- Precision is favoured, so common patterns are left out: 什么 + noun, 什么时候 inside a sentence, 哪里/哪儿 before a verb, and 谁 after a verb other than 是. They are too easily the indefinite "some" use.
- Single-word patterns such as `M` and `CD` must be written with a leading slash (`/M`, `/CD`), otherwise they are read as literal words. This was why the 几, 哪 and 块…毛 examples were missed.
- The tagger sometimes tags 几 as M (几百家) and 怎么样 as VA, so both are accepted.
- The whole-sentence `unless` lines are clause-limited, so 都 in a different clause does not block a match.
- 好/VA 几 is excluded because it appears in a counter-example sentence; 好/AD 几 is still allowed.
- 我是小李。 is only an answer sentence and is not matched, because 我是 + name is too general.

## 1-05
<!-- written by claude-sonnet-5-5 on 2026-11-07; edit freely -->
**Point:** A personal pronoun (我, 你, 您, 他, 她, 我们, 你们, 他们, 她们) placed directly before a noun, without 的, to show close possession (family, school, workplace). The basic pronoun use in the greetings 你好 and 您好 is also counted.

**Covers:** A pronoun at the start of a clause followed directly by a noun, as in 我家很大, 你哥哥很高, 他们公司在北京, 我们学校很大. Also 是 + pronoun + noun, as in 这是我女朋友. Also the standalone greetings 你好 and 您好 (a clause consisting only of the pronoun + 好).

**Does not cover:**
- Pronouns used as objects or after a verb or preposition, as in 请给我票, 等你, 代替他. These are not possession.
- A pronoun followed by a time word, adverb or verb, as in 他今天状态很好, 我周末去游泳.
- Pronoun + 的 + noun, which is ordinary possession with 的.
- 你好 or 您好 followed by more words in the same clause, such as 你好吗.

```rule
match: ^ 我|你|您|他|她|我们|你们|他们|她们/PN /NN
match: 是/VC 我|你|您|他|她|我们|你们|他们|她们/PN /NN
match: ^ 你|您/PN 好/VA $
```

**Notes:**
- The clause-start anchor is deliberate. It keeps out double-object sentences such as 给我票 and 告诉他消息, where PN + NN is indirect object + direct object.
- The noun slot is written as a bare `/NN`. The earlier `_/NN` form did not match anything, which is why every labeled example was missed.
- Known false-positive risks: topic-comment sentences like 我工作很忙 or 你作业做完了, where the noun is not strictly possessed. These are rare.
- The second `match:` line only fires after 是. Extending it to other verbs would catch double-object sentences.
- The greeting line requires 好 to end the clause, so 你好吗 and 你好看 are not matched.
- Not covered: a pronoun that follows a sentence-initial adverb or conjunction, such as 也我妈妈….

## 1-06
<!-- written by claude-sonnet-5-5 on 2026-11-07; edit freely -->
**Point:** Demonstrative pronouns 这 / 那 (and 这里/那里/这儿/那儿) used to point at things or places. The core pattern is 这/那 + measure word + noun, e.g. 这本书, 那个人.

**Covers:**
- 这/那 tagged DT followed directly by a measure word (这本书, 那个房间, 她喜欢那个书包).
- 这/那 as a pronoun subject before 是 at clause start (这是谁的手机？).
- The place words 这里/那里/这儿/那儿 tagged PN.

**Does not cover:**
- Sentences containing 这样, 那样, 这么, 那么 (the 2-07 words), or clause-initial 那 as a conjunction (那/AD).
- Filler 那个 at the end of a clause (那个⋯⋯).
- Name-calling or topic-category uses, where 这个/这种 follows a noun, pronoun or proper noun (你这个坏蛋, 上海这个城市). This includes 这种/这类 after a single clause-initial word (游泳这种运动).
- 那会儿 (a time expression).

```rule
match: 这|那/DT /M
match: ^ 这|那/PN 是/VC
match: 这里|那里|这儿|那儿/PN
unless: /PN|NN|NR 这|那/DT
unless: ^ _ 这|那/DT 种|类/M
unless: 那/DT 个/M $
unless: 这|那/DT 会/M 儿
unless: 这样|那样|这么|那么
unless: 这|那/DT 样/NN
unless: 那/AD
```

**Notes:**
- The unless lines are checked against the whole sentence. A sentence with a valid 这/那 + measure word is therefore dropped if it also has 那么, 这样, clause-initial 那 or a name-calling phrase. This is deliberate, because the labeled counter-examples include such sentences (实现这个目标应该不难，那么我们一起努力吧).
- The `/PN|NN|NR` before 这/那 also drops legitimate cases such as 给我这本书 (the object pronoun precedes). This is accepted in favour of precision.
- Clause-final 那个, as in 我喜欢那个。, is also dropped, since it can't be told apart from the filler use.
- 这/那 + 些 (这些, 那些) and 别的/有的 are not matched, because they have no measure-word pattern and tagging is unreliable.

## 1-08
<!-- written by claude-sonnet-5-5 on 2026-11-07; edit freely -->
**Point:** Counting nouns with a number plus a measure word (Number + M + Noun), including 个 with the number 一 omitted, and the money pattern 块/毛 and 多少钱.
**Covers:** A number followed by a common nominal measure word, such as 一个人, 四杯牛奶, 三条鱼, 两块五毛 and 三块. Also 是个 / 有个 + noun (他是个老外, 我有个儿子), and 多少钱 ("how much").
**Does not cover:** Sentences that also contain 本来, 根本, 人家, 中间 or 之间, because those belong to other grammar points and the labeled counter-examples include such sentences. Time-unit and verbal measure words (年, 天, 分钟, 次, 顿) are not matched. 口 is left out because it usually acts as a verbal measure (吃一口). Bare 这个/那个 + noun is also not matched, since it is a demonstrative use and not counting.
```rule
match: /CD 个|杯|本|家|间|块|页|条|只|瓶|台|支|颗|盒|件|张/M
match: 是/VC 个/M
match: 有/VE 个/M
match: 多少 钱
unless: 本来
unless: 根本
unless: 人家
unless: 中间
unless: 之间
```

**Notes:** The measure-word list is open-ended; add more nominal measure words (本, 辆, 双, 位, 种 and so on) if more coverage is wanted. 多少 is tagged CD and 钱 NN, so the 多少 钱 line matches the literal words. The `unless` lines are what keep the counter-example sentences out (e.g. 一个小孩子 with 根本, 一个沙发 with 中间, 多少户人家 with 人家). 一块儿/一起 should be tokenized as one word, so it should not trigger the 块 line; check this if the tagger splits it.

## 1-09
<!-- written by claude-sonnet-5-5 on 2026-11-07; edit freely -->
**Point:** Degree adverbs 非常 / 很 / 太 / 真 / 最 placed before an adjective or a psychological or modal verb, as in 很好, 最可爱了, 太累了, 真漂亮, 非常喜欢.

**Covers:** 很, 非常, 太, 真 or 最 directly followed by an adjective (VA) or a verb (VV or VE). That includes:
- Noun + 很 + Adj. (我们很累, 中文很难).
- 最 + Adj. (汉语最难).
- 太 + Adj. + 了 (老板太忙了).
- 真 + Adj. or 真 + 会 / 能 / 有 (你真能吃, 他家真有钱).
- 很 / 非常 / 最 + a psychological verb (我很想你, 我非常喜欢这本书, 谁最了解你).

**Does not cover:**
- 最好 ("had better"). It is tokenized as one word, and any sentence containing it is excluded.
- 很是 (很 followed by 是).
- Adj. + 得很 and other degree complements. The 得 before 很 excludes the whole sentence.
- 差很多 and 比…很多 comparisons.
- 很多 used as a quantity word (很/AD 多/CD), because 多 is not VA there.
- 很 + JJ, such as 很大的权.
- 真有你的.

```rule
match: 很|非常|太|真|最/AD /VA|VV|VE
unless: 最好
unless: 很 是
unless: 得 很
unless: 差 很
unless: 比 ... 很 多
unless: 真 有 _ 的
```

**Notes:** The unless lines are checked against the whole sentence, so they are deliberately blunt. A sentence like 这个活动很重要，我们最好穿得正式一点 is dropped because it contains 最好, even though 很重要 is a valid use; this favours precision. Tagging errors are tolerated by accepting VA, VV or VE after the adverb. Patterns with an intervening adverb (e.g. 很不舒服) are not matched, which is intentional. 真 + 是 is also not matched.

## 1-11
<!-- written by claude-sonnet-5-5 on 2026-11-07; edit freely -->
**Point:** Time adverbs before a verb: 在/正/正在 for an action in progress (including the 着…呢 and verb + object + 呢 forms), plus 先 ("first") and 马上 ("right away") before a verb.
**Covers:**
- 在, 正 or 正在 directly before a verb: 她在看书, 我正在吃饭, 他正上厕所呢.
- 在 tagged P directly before a verb after a subject: 他在上厕所.
- Verb + 着 … 呢: 老板开着会呢.
- Bare "Subj. + common action verb + object + 呢": 我吃饭呢, 爷爷看报纸呢.
- 先 or 马上 directly before a verb: 我先说吧, 医生马上来.
- 有时 tagged AD before a verb.

**Does not cover:**
- 正好 (a different word, covered by 2-20): "coincidentally" and "just right".
- 正 before 是, as in 现在正是吃饺子的时候, and 正常, 正式, 正确, 先生, 先后, 真正.
- 在 as a locative preposition followed by a place (在北京工作). It is not progressive and is excluded because it is borderline.
- Sentences that contain 正好 anywhere are excluded.

```rule
match: 在|正在|正/AD /VV
match: /PN|NN 在/P /VV
match: 先|马上|有时/AD /VV
match: /PN|NN /VV 着/AS ... 呢/SP
match: ^ /PN|NN 吃|喝|看|听|写|读|洗|上|开|做|打|玩|学|睡|唱|找|等|修|画/VV /NN /NN? 呢/SP
match: ^ /PN|NN 打电话|开会|上课|上班|洗澡|睡觉|做饭|开车/VV 呢/SP
unless: 正好
```

**Notes:** Verb + object + 呢 is limited to a list of concrete action verbs because a generic pattern would wrongly catch stative verbs like 喜欢 (我喜欢猫呢). The 着…呢 line may also match existential 着 (墙上挂着画呢). 在/P + place + verb (谁在里面洗澡) is not matched on purpose, since 他在家吃饭 is not progressive. Add that pattern only if you can restrict the place word. 在/P followed directly by a verb could occasionally be a nominal use (在学习上); this is rare.

## 1-12
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Frequency and repetition adverbs: 常、常常、再

**Covers:** 常/常常 (and 经常, 往往 as shown in the official wiki examples) expressing "often" or "usually"; 再 expressing "again" or "more" in future or sequential contexts; 又 + 是/要 for an upcoming repeat, as in the wiki comparison examples.

**Does not cover:** 一再/再三 (repetition emphasis); 再不…就/也… (last chance, even so); 再也不/没 (never again); 再…也… (no matter how); 再说 (furthermore).

```rule
match: 常/AD
match: 常常/AD
match: 经常/AD
match: 往往/AD
match: 再/AD
match: 再说/VV 一 遍
match: 又/AD 是|要
unless: 再/AD 也
unless: 再/AD 不
unless: 再/AD 没
unless: 再/AD ... 也
unless: 再说/AD
unless: 一再
unless: 再三
```

**Notes:** The earlier rule used patterns like `/AD 常 /VV`, which required an arbitrary adverb before 常 and a verb right after it, so it matched almost nothing. The rule now matches the adverb itself (常, 常常, 经常, 往往, 再, tagged AD), so negated forms, modal verbs and 比较 between the adverb and the verb no longer matter. The tagger sometimes merges 再说 + 一遍 into one verb (你可以再说一遍), so that case has its own line. 又 is limited to 又是 / 又要. The unless lines remove 再也不/没, 再不…就/也…, 再…也… and 再说 (in addition). 一再 and 再三 are single tokens and never match 再 on its own, but they are listed for safety.

## 1-14
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Negation with 不, 没/没有, 别, and 不要
**Covers:** Negating verbs and adjectives with 不, including A-not-A questions such as 想不想 and 是不是. Negating past actions with 没/没有. Negating the verb 有 with 没/没有. Negative commands with 别 or 不要.
**Does not cover:** Compounds and other constructions that use the same words: 不得不, 不怎么/没怎么, 不管, 不比, 不但, 岂不, 只好不, 少不了, 差不多, 不过, 不如, 不愧, 不屑, 要不是, 要不, 不到, 别说 (let alone), 不是…而是, comparisons with 没有 (没有 + noun + adjective), and potential complements (V+不+V).

```rule
match: /PN|NN|NT|M|LC|AD 不/AD /VV|VA|VC|AD
match: ^ 不/AD /VV|VA|VC
match: /PN|NN|NT|M|LC|AD 没有|没/VV /VV
match: /PN|NN|NT|M|LC|AD 没/AD /VV
match: ^ 没/AD /VV
match: /PN|NN|NT|M|LC|AD 没有|没/VE /NN
match: ^ 没有|没/VE /NN
match: 别/AD /VV
match: 不要/AD ... /VV
unless: 不得不/AD
unless: 不怎么/AD
unless: 不/AD 怎么/AD
unless: 没/AD 怎么/AD
unless: 不过/AD
unless: 差不多/AD
unless: 差不多/VV
unless: 少不了/AD
unless: 不如/P
unless: 不如/AD
unless: 不管/VV
unless: 不管/CS
unless: 不管/CC
unless: 不/AD 管/VV
unless: 不/AD 比/VV
unless: 不但/AD
unless: 不但/CC
unless: 岂不/AD
unless: 只好/AD
unless: 别说/VV
unless: 别说/AD
```
un

## 1-15
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Basic uses of 从 (cóng) meaning "from" in relation to place, time, source, or origin, plus 离 (lí) for distance from a place, time, or event.

**Covers:** 从...到 (from X to Y, for time or place); 从...中 (from a source, gaining a result); 从 + place/source + action (从我包里拿, 从哪儿来); 从...起/开始 and 从那以后/从今以后 (starting from); 离 + event/time/place + 还有/远/近 + duration.

**Does not cover:** 从来 as adverb meaning "always/never"; 从而 meaning "thus/thereby"; 自从 meaning "ever since"; 从...以来; 从...来看/来说; adverbial compounds 从小, 从此, 从前, 从中; 从事 (to engage in).

```rule
match: 从/P
match: 从今以后/NT
match: 离/P
unless: 从来
unless: 从而
unless: 自从
unless: 从小
unless: 从中
unless: 从此
unless: 从前
unless: 从事
unless: 从/P ... 以来
unless: 从/P ... 来看
unless: 从/P ... 来说
```

**Notes:**
- Tagged as a preposition (P), 从 is almost always the basic "from" use. The rule therefore matches 从/P directly rather than requiring a specific connector. The labeled examples show that 从 can be followed by 到/P, 到/VV, 起/LC, 开始/VV, 以后/LC, 里/LC, a noun like 身上, or just a verb (从哪儿来, 从这件事可以看出).
- 从今以后 is tokenized as a single NT, so it has its own match line.
- 离 tagged P is covered because the labeled examples (离毕业还有几个月, 离结婚还远呢, 上次见面离今天快十年了) belong to this point.
- Adverbial and other-point words (从来, 从而, 自从, 从小, 从中, 从此, 从前, 从事) are excluded by the unless lines. So are the 从...以来 and 从...来看/来说 frames, which belong to other points.

## 1-16
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Using 在 (zài) to indicate location of actions, existence, or position

**Covers:**
- Location before verb: 在 + place + verb (哥哥在北京学中文)
- Existence at location: subject + 在 + place (我在上海, 他不在学校)
- Location after special verbs (住/坐/站/放): verb + 在 + place (你住在上海吗, 衣服放在这里)
- Location with localizers (上/下/里/旁边): 在 + place + localizer (在桌子上)

**Does not cover:**
- Progressive aspect (在 as adverb, 正在): 她在看书
- 在于 (zàiyú, "lies in"): 成功在于坚持
- 早在 (zǎozài, "as early as"): 早在1942年就...
- 在...内 (time period): 在三年内
- 在...看来 (perspective): 在他看来
- 包括...在内 (including): 包括我在内

```rule
match: 在/P /NN|NR|PN
match: 放|住|坐|站/VV 在/P|VV /NN|NR|PN
match: ^ 在/VV /NN|NR /LC $
match: /PN|NN|NR 在/VV /NN|NR|LC
match: 不/AD 在/VV /NN|NR
match: /NT 在/VV /NN|NR
match: 想|要|会|能|可以|应该|喜欢/VV 在/VV /NN|NR
match: 在/VV /PN|NN|NR /NN 旁边/NN
unless: 早/AD 在/P
unless: 在/P ... 看来/VV
unless: 包括/VV ... 在/P
unless: 包括/VV ... 在内/VV
unless: 在内/VV
unless: 在于/VV
unless: 内/LC|NN|P
unless: 正在/AD
unless: 在/AD
unless: 在/P 上/VV
unless: 在/P 里面/NN
```

**Notes:** 在 is accepted as a preposition (P) before a place noun or pronoun, as a verb (VV) for existence, and after 住/坐/站/放 in either tag. Several extra patterns cover the tagger's habit of labelling 在 as VV, such as 不在学校, 现在在家 and 想在家睡觉. The unless lines remove 包括…在内, 在…内, 在…看来, 早在, 在于 and the progressive 在 (AD). Earlier unless lines used `.*`, which is not valid syntax, so they are now written with `...`. The 在/P 里面 and 在/P 上/VV exclusions are there because the labelled counter-examples 谁在里面洗澡 and 他在上厕所 must not match.

## 1-17
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** 跟/和 as "and" (joining nouns, verbs or adjectives), 跟/和 as "with" (跟 + person + verb, often with 一起), 跟/对 + person + 说, 对 + person + verb ("toward"), and the verbs 跟着 and 对着/对不上.
**Covers:** 
- 和/跟 as a conjunction: 手机和电脑都很贵, 预防和治理污染…
- 和/跟 as "with" before a verb: 我跟朋友去海滩了, 你想跟我一起去吗?, 不要跟我说话.
- 对 + person + 说/笑/发脾气 and similar: 他对我说…, 那个美女正对我笑呢.
- 跟着, 对着, and 对…不上 as verbs.

**Does not cover:** 
- Comparison patterns: 一样, 不一样, 差不多, 差很多, 不同/相同, 跟…比 and 比起来.
- 跟…似的.
- 跟/和…有关的.
- 对 as a preposition in other frames, such as 对…感兴趣 or 对…很好. Only 说, 笑, 点头, 道歉 and 发 are allowed after 对 + noun.
- These are separate grammar points, so any sentence containing those words is excluded entirely.

```rule
match: 和|跟/CC
match: 和/P
match: 跟/P
match: 对/P _ 说|笑|点头|道歉|发
match: 对/VV 着/AS
match: 对/VA 着/AS
match: 跟/VV 着/AS
match: 对/VV 不/AD 上/VV
unless: 一样
unless: 差不多
unless: 差
unless: 似的
unless: 有关
unless: 比
unless: 比起来
unless: 不同
unless: 相同
```

**Notes:** 
- The `unless` lines check the whole sentence, so a sentence that also contains 一样 or 比 is dropped even if it shows 跟 as "with". For example, 你跟你妈一样喜欢买衣服 is lost this way. That cost is accepted to keep precision high.
- 对 + noun + verb is deliberately narrow, with only a short verb list. Widen the list (for example 笑着, 鞠躬) only after checking it doesn't pull in 对…感兴趣 or 对…有好处.
- The tagger gives 和/跟 either CC or P, so both tags are matched.
- 对着/跟着 are matched as VV/VA plus the particle 着.

## 1-19
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Linking words 跟/和 ("and", "with") and 还是 ("or" in questions).

**Covers:**
- 还是 as "or" between two options (还是/CC after something in the same clause), e.g. 你喝茶还是喝水？ 这是水还是酒？
- A bare "pronoun 还是 pronoun" clause, e.g. 我还是他？
- 和 or 跟 as "and" between nouns (和/CC, 跟/CC), e.g. 手机和电脑都很贵。
- 或者 as "or" in statements.
- 跟 as "with" (跟/P) before a verb, e.g. 我昨天跟朋友去海滩了。 你想跟我一起去吗？ 不要跟我说话！

**Does not cover:**
- 还是/AD meaning "still" or "had better".
- 还是 at the start of a clause, which the tagger sometimes tags CC, as in 他闻了又闻，还是什么都没闻到。
- 跟/和 in comparisons: 一样, 差不多, 差很多, 比/比起来, 似的, 有关.
- Age comparisons such as 你爸爸和你妈妈谁大？
- "A 跟/和 B 是 …" where the 是 follows the noun pair directly, as in 鲜花跟巧克力是最好的礼物。
- 跟 + person + 说 ("tell"), which belongs to the 跟/对 contrast point.
- 和/P, which is ambiguous with these other uses.

```rule
match: _ 还是/CC _
match: ^ /PN 还是/AD /PN $
match: _ 和/CC _
match: _ 跟/CC _
match: _ 或者/CC _
match: 跟/P
unless: 一样
unless: 差不多
unless: 差/VV
unless: 比
unless: 比起来
unless: 似的
unless: 有关
unless: 说/VV
unless: 要 跟/P 我 一起
unless: 跟|和/CC _ 是/VC
unless: 跟|和/CC _ _ 是/VC
unless: 谁 大/VA
```

**Notes:**
- The pattern `_ 还是/CC` needs a word before 还是 in the same clause. This deliberately excludes clause-initial 还是 mis-tagged as CC.
- `unless:` lines are checked against the whole sentence, so they are blunt. A sentence with both a good 还是 question and a stray 说 or 比 will be dropped, which is acceptable for precision.
- The `要 跟/P 我 一起` exclusion exists only because the labeled counter-example 你要跟我一起去吗？ conflicts with the positive 你想跟我一起去吗？ and the two cannot be told apart structurally. Remove it if that counter-example turns out to be label noise.
- The `跟|和/CC _ 是/VC` and `跟|和/CC _ _ 是/VC` exclusions drop "A和B是…" sentences such as 鲜花跟巧克力是最好的礼物。 Sentences with 都 between the noun pair and 是 (你爸爸和你妈妈都是美国人吗？) are still kept, because 都 pushes 是 out of the pattern's range.
- `谁 大/VA` removes the age-comparison question 你爸爸和你妈妈谁大？
- 还是/AD in the bare 我还是他？ is matched only by the narrow pronoun-only clause pattern.
- 和/P ("请和我联络") is left out because it cannot be reliably separated from the 一样/差不多/有关 uses.

## 1-20
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** The structural particles 的 (links a modifier to a noun), 地 (links an adverbial to a verb) and 得 (links a verb to a degree complement).

**Covers:**
- 的 between a modifier and a following noun: possessive (我的手机), adjective (漂亮的衣服), or phrase (你教的学生, 妈妈做的菜).
- 地 (tagged DEV) before a verb: 认真地听, 慢慢地走.
- 得 (DER) after a verb or adjective, followed by an adverb or adjective: 做得很好, 开得很快.

**Does not cover:**
- Modal or sentence-final 的 (你可以的, 他不会道歉的). It has no following noun and is tagged SP. Any clause that ends in such a 的 is skipped, even if it also has a regular 的 earlier.
- 的话 and 有的 / 有的是. Any sentence containing these words is skipped.
- 什么的 and 之类的 lists.
- 似的 comparisons like 热得跟…似的.
- The 是…的 constructions. Sentences with 是 and a clause-final 的 are excluded.
- Bare AA / AABB reduplication, and the nominalized "X的" with no noun. Detecting these is too error-prone.

```rule
match: _ 的/DEG|DEC /NN|NR|NT|JJ|CD|DT|PN
match: /VV 的/AS /NN|NR|NT
match: 地/DEV
match: /VV|VA 得/DER /AD|VA
unless: 什么 的
unless: 什么的
unless: 类/M 的/DEG
unless: 似的
unless: 有的
unless: 的话
unless: 的/SP $
unless: 是/VC ... 的/DEG|DEC $
```

**Notes:**
- Precision is favoured over recall. Labeled examples where 的 is tagged SP and sits at the end of a clause (我画的) are deliberately not matched. They look identical to the modal 的 in counter-examples such as 你可以的.
- The `unless: 的/SP $` line also rejects sentences like 你的建议我们会考虑的 and 孩子们学得挺快的. This is intentional, because the clause-final 的 is the modal or sentence-final kind.
- The `unless: 是/VC ... 的 $` line also rejects a sentence that has a good 的 earlier (他家的家具都是中式的). This is intentional, to keep 是…的 sentences out.
- 有的 and 的话 are single tokens, so the `unless` lines match them as whole words. This keeps 有的…得 and 有的是 sentences out.
- 什么 的 is matched without a tag restriction, because the tagger gives that 的 either DEG or DEC.
- 得 is restricted to a verb or adjective followed by AD or VA, so 得 meaning "must/obtain" (VV) does not match.
- The 的/AS line is there because the tagger sometimes tags 的 after a verb as AS (你教的学生).

## 1-21
<!-- written by claude-haiku-4-5 on 2026-11-09; edit freely -->
**Point:** Aspect particle 了 (le) expressing completion of an action
**Covers:** Verb + 了 + Object constructions where 了 marks the completion of an action. Includes: (1) Verb + 了 + [Number + Measure Word] + Object; (2) Verb + 了 + Duration + Object; (3) Verb + 了 + Object (simple objects); (4) Verb + 了 + Object + 了 (le at end); (5) questions with 了吗; (6) sentences with time expressions, modifiers, or complements between verb and object.
**Does not cover:** (1) Change of state (adjective + 了, intransitive verbs + 了 with no object); (2) Expressing "now" or realization (知道了, 懂了); (3) Expressing "not anymore" (不/没 + verb + 了); (4) Potential complements (得了, 不了, 得起, 不起); (5) Degree complements (极了, 死了, 坏了); (6) Duration expressions of ongoing actions; (7) About-to-happen (快了, 快要了); (8) Other uses of 了 that are not aspect particles (e.g., 为了, 除了, 极了, 太...了)

```rule
match: /VV .* /AS .* /NN
match: /VV .* /AS .* /SP
unless: /VA .* /AS .* /SP
unless: /VA .* /VV .* /AS .* /SP
unless: /AD 快 .* /VV .* /AS .* /SP
unless: /AD 不 .* /VV .* /AS .* /SP
unless: /AD 没 .* /VV .* /AS .* /SP
unless: /VV .* 得 /VV
unless: /VV .* 不 /AD .* /VV
unless: P 除了
unless: P 为了
```

**Notes:** The rule matches verbs (VV) followed by 了 as aspect particle (AS) and then either a noun (NN) or sentence-final particle (SP like 吗). It excludes constructions where 了 indicates change of state (adjective predicates), potential complements (得/不 patterns), imperative/prohibition patterns (不/没 before verb), about-to-happen expressions (快), and prepositions like 除了 or 为了. The pattern allows any intervening material (numbers, measures, duration, modifiers, complements) between verb and object since all of these are attested in labeled examples.

## 1-36
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** 是-sentences for connecting nouns, and "是……的" sentences of purpose (来/去 + Verb + 的, 用来 + Verb + 的, 给 + Person + Verb + 的)
**Covers:** Subject + 是 + Noun/Pronoun (他是老师, 这是他的书); Person + 是 + 来/去 + Verb + 的 (我是来实习的); Thing + 是 + 用来 + Verb + 的 (钱是用来花的); Thing + 是 + 给 + Person + Verb + 的 (房间是给客人住的)
**Does not cover:** 于是, 凡是, 要不是, 要是, 还是, 不是…就是, 老是/总是, 很是, 有的是, 就是, 岂不是, 是否, 不愧是, 不是…而是, and other 是 patterns in the exclusion list. It also misses 是…吗 and 是不是 questions, adjective + 的 sentences, and location + 是 sentences, to keep precision high.

```rule
match: /PN|NN|NR 是/VC /NN|NR|PN|DT|CD
match: 是/VC 来|去/VV ... 的/SP|DEC|DEG
match: 是/VC 用来/VV ... 的/SP|DEC|DEG
match: 是/VC 给/P ... 的/SP|DEC|DEG
unless: 于是 凡是 要是 老是 总是 很是 有的是 就是 是否 不愧 还是 或是 为的是 岂不 那倒
unless: 就/AD 是/VC
unless: 很/AD 是/VC
unless: 要 不 是
unless: 不 是 ... 就
unless: 不 是 ... 而是
```

**Notes:** The match patterns are deliberately narrow. 是 must directly follow a pronoun or noun and be followed by a noun-like word, or it must appear in a purpose pattern ending in 的. Because 是 right after 不, 很, 就, 岂不 or 要不 is not preceded by a PN/NN/NR, most compound-adverb uses are not matched in the first place. The unless lines use tokenized forms (e.g. 就/AD 是/VC, 要 不 是) because the segmenter splits words like 要不是, 岂不是 and 很是. They remove sentences where another construction co-occurs with a plain 是 clause.

## 1-37
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** The 有字句 as the basic verb 有: possession (Subj. + 有 + Obj.), existence (Place + 有 + Obj.), and negation with 没(有).
**Covers:** 有, 没有 or 没 tagged as a verb (VE or VV) directly followed by a noun, proper noun, time noun, number, noun modifier, or 很 plus a degree word. Also 有 + pronoun + 的 (e.g. 有你的号码). Also 有/没有 directly followed by the question particle 吗 when the object is missing from the tagging (e.g. 你的老师有iPad吗, where the foreign word is dropped). Examples: 我有钱, 他没有哥哥, 房间里有人吗, 我们有三个女儿, 日本有很多中国人.
**Does not cover:**
- 有点(儿) and 有一点 + adjective, and 有一点 + noun (excluded to be safe).
- 有 + verb phrase (Taiwanese 有 + V, e.g. 有见过).
- Comparisons: 有 or 没有 + NP + adjective (他有我帅, 你有老板忙吗, 纽约人口有上海人口多吗); 有 + NP + 这么/那么 + adjective; 有 + proper name + 吗 (他有姚明高吗).
- 没有 + verb (past negation) and 没有 tagged P or AD.
- Any sentence containing 有的, 有的是, 有关, 只有, 所有, 具有.
- A clause-final 还有 ("in addition"), including the sentence it appears in.
- Fixed 很/真/太/最/挺/特别/非常 + 有 (很有道理). It is not the possession/existence point.

```rule
match: 有|没有|没/VE|VV /NN|NR|NT|CD|JJ
match: 有|没有|没/VE|VV /PN 的
match: 有|没有|没/VE|VV 很/AD
match: 有|没有|没/VE 吗/SP
unless: 有 点
unless: 有 点儿
unless: 有 一 点
unless: 没有 有
unless: 有|没有|没 /PN|NN|NR /NN? /VA
unless: 有|没有|没 /PN|NN|NR /NR|NN /NN? /VA
unless: 有|没有|没 /NR 吗/SP
unless: 有|没有|没 ... 那么|这么
unless: 很|非常|特别|真|太|最|挺 有
unless: 有的
unless: 还/AD 有 $
unless: 有 激动
```

**Notes:**
- The tagger sometimes labels 没 as AD (我没工作), so those sentences are missed. This is accepted for precision.
- The comparison exclusion (有/没有 + NP + VA) also blocks sentences like 我有钱很开心, which is acceptable.
- 有的 anywhere in the sentence blocks the match, even if an earlier clause is an ordinary 有 sentence (我们公司有一些电脑，有的是新的). This is deliberate: those sentences are used to teach 有的.
- 还有 + noun can still match, which is fine for existence. A bare 还有 at the end of a clause (the "in addition" use) blocks the whole sentence.
- 有 + 吗 is accepted without an object because the tagger can drop foreign words such as iPad.
- 有激动 is blocked explicitly because 激动 is mis-tagged as a noun.
- Because a pattern only matches inside one clause, the unless lines other than 有的 and 还有 do not look across commas.

## 1-45
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Yes-no questions, tag questions, and questions about completed actions using 吗
**Covers:** Statements converted to yes-no questions by adding 吗 at the end; tag questions with confirmation words (好/对/是/可以) + 吗; questions about completed actions with 了吗
**Does not cover:** Other uses of 吗 that are not questions; 吗 used as a pronoun (e.g. 你干吗去); questions formed with other methods (interrogative words like 谁/什么/哪儿 without 吗)

```rule
match: 吗/SP $
```

**Notes:** The original rule was malformed (`... /SP $吗`) and matched none of the labeled examples. The fixed rule matches 吗 tagged as SP at the end of a clause. This covers plain yes-no questions, tag questions after a comma (好吗, 对吗, 是吗, 可以吗), and 了吗 questions. 干吗 is tokenized as one word, not as 吗/SP, so it is not matched and needs no separate exclusion. I dropped the old `unless` line, which was based on a wrong assumption and could wrongly block valid questions.

## 1-47
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Using 还是 to offer choices in questions

**Covers:** Questions with 还是/CC presenting two options where the asker expects the listener to choose one. This includes questions with explicit verbs, implicit verbs, or adjective predicates, as well as rhetorical and indirect questions (with 知道, 想知道, 不确定, etc.).

**Does not cover:** 还是 as an adverb meaning "still" or "had better"; 还是 in statements (only questions); 还是 after 又 showing repeated past actions; 还是 after 尽管 expressing concession; 还是 at the start of a clause as a suggestion (还是……吧).

```rule
match: _ 还是/CC
unless: /CS ... 还是/CC
unless: _ 还是/AD
unless: /AS 又|CC _ ... 还是/AD
unless: 还是/AD _ /SP
unless: /SP $ 
unless: /VV 了/AS 又
```

**Notes:** The rule matches 还是 tagged CC (coordinating conjunction) only when at least one word precedes it in the same clause. A real choice question has Option A before 还是. A clause-initial 还是, as in "她不想说，还是不要再问了", is the "had better" use of 还是 and is no longer matched. The unless lines remove the following cases:
- Clauses after 尽管 or another CS (subordinating conjunction).
- 还是 tagged AD (the "still" adverb).
- Repeated-action patterns with 又.
- Clauses ending in a sentence-final particle.

Bare 还是 pairs that the tagger mis-tags as AD (e.g. 我还是他？) are deliberately not matched, to keep precision. Statements with 或者 are not matched either, because 或者 is not 还是.

## 2-02
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Modal verbs 应该 and 该 expressing "should" or obligation

**Covers:** Sentences where 应该 or 该 is tagged as a verb (VV) and is followed, in the same clause, by another verb. The second verb is the action someone should or should not do. This includes negatives with 不 and sentences where a time phrase or object sits between the modal and the verb. It also includes 你该吃药了, which is an official example of this point.

**Does not cover:** Uses of 该 that are not tagged as a verb, such as the determiner in 该公司 ("the said company"). It also does not cover 应该/该 with no following verb, such as 应该很好, where an adjective follows.

```rule
match: 应该/VV ... /VV
match: 该/VV ... /VV
```

**Notes:** The old rule required exactly one word between the modal and the verb, so it missed almost every sentence. Examples such as 应该喝白酒 and 不应该迟到 have the verb directly after the modal. The new rule allows any number of words in between, which also covers 应该八点半来公司. The old `unless` clause is removed because it excluded the official example 你该吃药了. 该⋯⋯了 (2-80) cannot be reliably separated from this point on structure alone, so a sentence like 该走了 can still match.

## 2-04
<!-- written by claude-haiku-4-5 on 2026-11-09; edit freely -->
**Point:** Reduplication of verbs to express brief action or "a little bit"
**Covers:** AA pattern (verb repeated twice with neutral tone on second), A一A pattern (verb + 一 + same verb), A了A pattern (verb + 了 + same verb), and ABAB pattern (two-syllable verb repeated entirely)
**Does not cover:** Sentences where 一 appears as a number/quantifier separate from verb reduplication, or where 了 is only a perfective aspect marker without verb reduplication, or other grammar patterns that happen to use repeated words

```rule
match: /VV /VV
match: /VV CD|AD /VV
match: /VV AS /VV
unless: ... CD M ...
unless: ... CD 个|部|场|次|遍|回|下|堆|番|篇 ...
unless: /VV AS ... /VV
```

**Notes:** 
- The first match pattern catches AA reduplication (two consecutive verbs, same or closely related)
- The second match catches A一A and similar patterns where 一/CD or 一/AD appears between two verbs
- The third match catches A了A patterns where 了/AS appears between verbs
- The first unless excludes cases like "一个" or "一场" which are measure word constructions, not verb reduplication
- The second unless specifically excludes measure word phrases that might falsely trigger the pattern
- The third unless excludes sentences where 了 indicates perfective aspect on a single verb (not verb reduplication) followed by other content
- Tagger sometimes marks 一 as AD or CD depending on context; both should match
- Two-syllable verbs should still match as /VV /VV when both parts are tagged separately
- Cases where the repeated verb is part of a larger compound (like 出去 走 走) still match because each word is tagged /VV separately

## 2-05
<!-- written by claude-haiku-4-5 on 2026-11-09; edit freely -->
**Point:** Questioning words 怎么样/怎样, 为什么, 多久 used to ask open-ended questions about how something is, why something happened, or how long something took.

**Covers:** 
- 怎么样/怎样 asking "how is X?" or "what is X like?" (with or without 觉得)
- 为什么 asking "why?"
- 多久 asking "how long?"

**Does not cover:** 
- 怎么样/怎样 used as an adverbial modifier meaning "in some way" or in the phrase 不怎么样 (meaning "not very good")
- These words in statements rather than questions (e.g., "这个电影不怎么样" is an opinion statement, not asking a question)

```rule
match: 怎么样|怎样/VV ... \?
match: 多/AD 久/VA ... \?
match: 为什么/AD ... \?
unless: 不/AD 怎么样|怎样
```

**Notes:** The rule matches these question words when they appear tagged as VV (for 怎么样/怎样) or AD + VA (for 多久) or AD (for 为什么) in sentences ending with question marks. The unless clause excludes 不怎么样 constructions, which express opinions rather than asking genuine questions about how something is. The tagger occasionally tags 怎样 as VA rather than VV, but the question mark ending helps distinguish genuine questions from statements like "这个电影不怎么样" where the question mark would not appear.

## 2-07
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Demonstrative adverbs 这样/那样/这么/那么 expressing manner, degree, or hypothetical condition

**Covers:**
- 这样/那样 + verb (manner: "this/that way")
- 这么/那么 + adjective (degree emphasis: "so/that")
- 这样 in consequential clauses with 会/才 ("this way will/only")
- 这样的话 for hypothetical "if this way"

**Does not cover:** 那么 used as a discourse connector meaning "then/so" in response to a preceding clause (那么 + suggestion/decision), which is a different grammar point about logical sequence rather than manner/degree/hypothesis

```rule
match: 这样|那样/AD ... /VV|VA
match: 这样 的话
match: 这么|那么/AD /VA|VE
unless: ^ 那么/AD
```

**Notes:**
- The old rule never matched because of syntax errors. Parentheses around the alternatives and bare `VA|VV` without a leading slash are not valid, so every pattern failed.
- The first line covers 这样/那样 followed by a verb or adjective in the same clause. This includes the 这样 + 会/才 consequence pattern and 是这样想的.
- The second line covers 这样的话 whatever tag 这样 or 的话 receives. The tagger gives VV, VA or AD for 这样, and AD or SP for 的话.
- The third line covers 这么/那么 immediately before an adjective or 没/有 (这么冷, 那么多, 这么没礼貌). Requiring the adjective right after the word keeps out connector uses.
- The unless line blocks 那么 at the start of a clause. That position is where the "then…" connector (那么 + suggestion/decision) appears, and it is the other grammar point.

## 2-09
<!-- written by claude-haiku-4-5 on 2026-11-09; edit freely -->
**Point:** Large numbers using 千, 万, 亿 as units in numeric expressions
**Covers:** Numbers expressed using 千 (thousand), 万 (ten thousand), and 亿 (hundred million) as numeric units, where these words form part of a complete number value tagged as CD (cardinal number)
**Does not cover:** 千万 as an adverb meaning "must" or "make sure"; 万一 as a subordinating conjunction meaning "if" or "what if"; 万一 as an adverb in conditional expressions; idiomatic uses like "以防万一"

```rule
match: /CD
unless: 千万/CD /VV|AD
unless: 万一/AD
unless: 万一/CS
unless: 以/P 防/VV 万一/AD
```

**Notes:** The rule matches any clause containing a single /CD tag, which in the context of this point represents complete numeric expressions like "十四亿" or "五万两千一百五十二". The unless clauses exclude: (1) 千万 followed by a verb or adverb, which indicates the "make sure/must" usage; (2) 万一 tagged as AD (adverb), indicating conditional "what if" usage; (3) 万一 tagged as CS (subordinating conjunction), indicating conditional usage; (4) the phrase "以防万一" which is an idiomatic expression. The tagger appears to sometimes tag 万一 as CS in conditional contexts, and this should be excluded.

## 2-11
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Measure words for verbs (动量词): 遍, 次, 场, 回, 下 and "一下" for brief actions
**Covers:** (1) Verb + (了/过) + Number + measure word (遍/次/场/回/下) showing frequency or repetition of an action, including patterns like 一百多遍, 好几下, 很多次; (2) Verb + 一下 expressing a brief or softened action; (3) "how often" questions of the form 多长时间 + Verb + 一个/一次
**Does not cover:** 一下子 (sudden change), 下去 (result complement / continuation), potential complements with 得/不, location words (在...下/里), time/amount limits (以下/以上), or other uses of 下 as direction/location

```rule
match: /VV /AS? /CD /CD? 遍|次|回|场|下/M
match: /VV /AS? /AD /CD 遍|次|回|场|下/M
match: /VV /AS? /PN? 很/AD 多 遍|次|回|场|下/M
match: 多/AD 长/JJ 时间/NN /VV 一/CD 个/M
unless: 下去
unless: 下/M 子
unless: 一下子
unless: 以下
```

**Notes:** 
- The main line matches a verb, an optional aspect particle (了/过), a number (optionally followed by 多, as in 一百多), and then a verb measure word tagged M (遍/次/回/场/下). This covers 说一遍, 说过一百多遍, 打了我三下 and 休息一下.
- Verbs are allowed to take a pronoun object before the measure word, as in 问了他很多次. This is handled only for the 很多 form, to keep precision.
- 好几下 is covered through the AD + CD form.
- The "how often" frame 多长时间 + Verb + 一个 is covered by a specific line, because 个 is not otherwise a verbal measure word.
- Exclusions: 下去 (result complement), 一下子 (suddenly), and 以下 (limit).
- Time-word + 一/CD + M patterns such as 一周 or 两个月 are not matched on their own, because the number and measure word must directly follow a verb.
- The sentence 我们休息一下吧 also has 一下, so it is kept as a match. It is a genuine brief-action use.

## 2-12
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Expressing the duration of a completed action with 了 and a time measure (verb + 了 + duration + optional 的 + object)

**Covers:** Sentences with the pattern: Verb + 了 (aspect marker) + number + duration word (年, 天, 周, 分钟, 小时, 个月, 星期, or time nouns such as 晚上, 上午, 下午) + optional 的 + object. The duration says how long the completed action lasted. A time word before the verb (昨天, 刚才) is allowed.

**Does not cover:** Duration words with no 了 after the verb (e.g. 清洁工每天打扫); 一年一度 and similar 一度/一时 uses; sentences with a sentence-final 了 (e.g. 我等了三天了), which are a different pattern for ongoing actions; time words that give a point in time rather than a duration (今天, 明年).

```rule
match: /VV 了/AS /CD 个? 年|天|周|分钟|小时|钟头|星期|月|晚上|上午|下午|早上|中午|夜
unless: /SP $
```

**Notes:** The old rule required an extra word between the number and the measure word, so it never matched any of the labeled examples. The new rule puts the number directly after the verb and 了, with an optional 个, then a specific list of duration words. That list covers measure words (年/天), nouns tagged NN (小时), and time nouns tagged NT (晚上/上午). Because the pattern is not anchored to the end of the clause, it matches both the version with an object and the version with 的 plus an object. The old VA exclusion is dropped: the pattern already fails on 一年一度, and the exclusion would wrongly reject sentences that merely contain an adjective elsewhere. The exclusion for a clause-final sentence particle 了 is kept.

## 2-13
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Degree adverbs (多、多么、好、更、十分、特别、挺、有（一）点儿) to express intensity, comparison, or mild complaint
**Covers:** Using 更 (and 比…还) before adjectives to mean "even more"; using 挺 before adjectives/psych verbs to mean "quite"; using 有点/有一点 before adjectives to mean "a little too"; using 多么 before adjectives; using 特别/十分 before adjectives
**Does not cover:** 更不用说 (let alone) pattern; 不但...更 (not only...but also) pattern; 更 as part of 更新/更改 (verb meanings change/update); 一点 after adjectives meaning "a little bit" in comparisons; 一点 before nouns meaning quantity; 点 as a measure word or time unit

```rule
match: 更|挺|多么|特别|十分/AD /VA|VV
match: 挺/AD 有/VE
match: 比 ... 还/AD /VA
match: 有/VE 点|点儿 不? /VA|VV
match: 有/VE 一 点|点儿 不? /VA|VV
unless: 更 不用 说
unless: 不但
unless: 连 ... 都|也 ... 更 不用 说
```

**Notes:** The original rule did not match because it used parentheses (not supported) and put a wildcard between the adverb and the predicate, though the adverb sits directly before it. The first line now matches 更/挺/多么/特别/十分 directly before an adjective or verb (including psych verbs like 喜欢, 怕, 后悔). Line 2 covers 挺有钱, where 有 is tagged VE. Line 3 covers 比…还 + adjective ("even more"). The bare 还 + verb "still" reading is not matched. Lines 4 and 5 cover 有点/有一点(儿), with optional 不 before the predicate. The match requires a predicate right after 点, so 有点饭 and 有一点番茄酱 (quantity) are not matched, and neither is 一点 after an adjective (慢一点, 重一点). The unless lines exclude 更不用说 and 不但…更.

## 2-14
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Scope/coordination adverbs 全, 一共, 只 for indicating "all/whole," "altogether," or "only"

**Covers:**
- 全 / 全都 / 全+noun (全家, 全校, 全身, 全年, 全公司的人) followed by 都, meaning "all / whole"
- 只 / 只能 / 只要 meaning "only", placed after a subject and before a verb or auxiliary (我只能说两句中文; 我老婆只要一个孩子)
- 一共 meaning "altogether / in total"

**Does not cover:**
- 只好 (had no choice but)
- 只要 meaning "as long as" (只要……就……)
- 只有……才 (only if), including when 只有 and 才 fall in different clauses (this is why bare 只有/AD is not matched)
- 只 as a measure word (一只猫)
- 不只 / 不但 (not only…but also)
- 完全, 全力 and other words that merely contain 全

```rule
match: (全|全都|一共)/AD
match: 全/DT _ ... 都/AD
match: (全校|全身|全家|全班|全国|全年|全世界|全公司) ... 都/AD
match: (只|只能)/AD
match: _ 只要/AD
match: PN 只要/VV NN
unless: 只要 ... 就
unless: 只有 ... 才
unless: 不只
unless: 不/AD 只/AD
unless: 只/M
unless: 只好
unless: 只是/VC
```

**Notes:**
- The 全 patterns cover the tagging variants: 全/DT + noun/measure + 都, 全校/NN, 全身/AD, 全/AD and 全都/AD.
- 只有 is deliberately not matched on its own. The 只有……才 sentences in the data split 只有 and 才 across clauses, so an unless: line cannot catch them, and bare 只有 is too risky.
- 只要 is matched only when it follows a subject. Clause-initial 只要 is almost always the "as long as" condition, so it is skipped.
- Within a clause, a 就 after 只要 rules out the "as long as" reading.
- 只好, 不只 and 只 tagged as a measure word are excluded.

## 2-15
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Time adverbs expressing "already" (已经), "all along" (一直), "just" (刚/刚刚), and "suddenly" (忽然/突然)

**Covers:**
- 已经 with verb phrases, adjectives, or time expressions (with or without negation and 了)
- 一直 expressing continuous or habitual actions or states
- 刚/刚刚 expressing actions in the immediate past
- 忽然/突然 expressing sudden or unexpected actions, states, or adjectives

**Does not cover:**
- 刚才 ("just now" as a time point). This is a different grammar point.
- 刚好 (coincidence or luck). This is an HSK 6 grammar point.
- Other uses of these words outside their adverbial time function.

```rule
match: 刚|刚刚|已经|一直|忽然|突然/AD
unless: 刚才
```

**Notes:**
- The earlier rule put the tag before the word list (`/AD (…)`), so it matched nothing. The syntax is now `word|word/AD`, which matches any of the six adverbs tagged AD.
- The rule matches the adverb anywhere in a clause. It does not require 了 or any other element, so all the patterns are covered: 已经 + verb, adjective, time or duration ± 了, 已经 + 不, 一直 + predicate, 刚/刚刚 + verb ± duration, and 忽然/突然 + verb or adjective.
- Sentences containing 刚才 are excluded. This is a deliberate precision-first choice, even though it may drop a sentence that also contains one of the other adverbs.
- 刚好 is tokenized as a single word, so it does not match.
- 突然 used as an adjective or complement is not tagged AD and is not matched.

## 2-16
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Frequency and repetition adverbs: 重新, 经常, 老, 老是, 又

**Covers:** 
- 老是/老 expressing habitual or repeated action with a frustrated tone
- 经常/常常 expressing frequent action, including 经常 的 as a property ("是经常的事情")
- 又 expressing an action happening again (past or imminent)
- 重新 expressing doing something again from the start

**Does not cover:**
- 又 used for emphasis of negation (又不/又没 meaning "definitely not")
- 又 in repeated action patterns with 了...又... structure
- 又 expressing both A and B qualities (又...又... with adjectives)
- 既...又... patterns
- 老 as an adjective meaning "old" (老师, 老人, 老家)
- 常 as an adjective meaning "frequent/regular"

```rule
match: 老是/AD ... /VV|VA
match: 老/AD ... /VV|VA
match: 经常/AD ... /VV
match: 常常/AD ... /VV
match: 常/AD /VV
match: 经常/VA 的/DEC
match: 又/AD /VV
match: 又/AD /VA 了/SP
match: 重新/AD ... /VV
unless: 既/AD ... 又/AD
unless: 又/AD ... 又/AD
unless: 又/CC
unless: /VV 了/AS 又/AD /VV
unless: /VV 了/AS 又/CC
```

**Notes:** 
- The pattern for 老/老是 requires a verb or adjective after to distinguish from the noun adjective uses (老师, 老人). Often there are adverbs between (不, 很, 这么) but these aren't required.
- 又 is matched only when directly followed by a verb (又下雨了, 又迟到了, 又要/能/可以...) or by an adjective plus sentence-final 了. 又不/又没 are deliberately not matched. They are mostly the emphatic-negation use ("又不知道", "又没下雨"), which belongs to a different point, so this rule misses some genuine "again + negative" sentences.
- Any clause with two 又, or with 既 and 又, is excluded (又A又B, 既A又B).
- Exclude 了…又… repeated-action patterns (看了又看).
- 经常/VA 的 (frequency as a property, e.g. 是经常的事情) counts as the grammar point.
- 常 by itself (single character) counts when followed by a verb, used in colloquial speech.

## 2-17
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Expressing "then" with the adverb 就 (jiù) in conditional and causal structures

**Covers:** Sentences whose first clause sets up a condition or cause (如果, 要是, 假如, 既然, 只要, or a plain fact or situation), and whose second clause, usually led by 就, gives the consequence, result or suggestion. This includes "if...then", "since...then", "as long as...then" and simple cause-then-result sequences.

**Does not cover:** 就 for earliness or timing (他早就知道了), scope (就这个问题), small quantity (就他知道), near-future 就要...了, "even if" patterns (就算/就是...也), emphasis with 就是, "as you like" and verb-repetition patterns (想吃就吃, 要买就买), 不是...就是, 一...就, and verbs repeated with 着 (说着说着就...).

```rule
match: 如果|既然|只要|假如|要是/CS
match: ^ 就/AD /VV|P|AD
match: ^ /PN 就/AD /VV|P|AD
unless: 就是
unless: 就算
unless: 就/AD 是/VC
unless: 不是 ... 就是
unless: 就/AD 要/VV ... 了/SP|AS $
unless: 一/AD ... 就/AD
unless: 着/AS 就/AD
unless: 才/AD
unless: 就/AD 在/VV
```

**Notes:** Patterns match within a single clause, so they cannot link a first clause to a second. The first line therefore marks a conditional or causal conjunction clause (如果, 既然, 只要, 假如, 要是). The second and third lines catch 就 as the lead item of a result clause, either alone or right after a pronoun subject, followed by a verb, preposition or adverb. The unless lines exclude the nearby non-target uses of 就: emphasis 就是, 就算, 就要...了, 不是...就是, 一...就, 着 repetition, 才 contrasts and verbal 在. The main trade-off is that a sentence with an explicit conjunction but no 就 still matches, which is correct for the 只要, 既然 and 如果 examples that carry their 就 in the other clause.

## 2-19
<!-- written by claude-haiku-4-5 on 2026-11-09; edit freely -->
**Point:** Modal adverbs expressing approximation (差不多), similarity (差不多), and appearance/seeming (好像)
**Covers:** 差不多 used as an adverb or predicate to mean "more or less," "almost the same," or "approximately"; 好像 used as an adverb to mean "seems like" or "it seems that"
**Does not cover:** 一定 and 必须 (different modal meanings); 也许 (expressing possibility); 差不多/好像 when tagged as other parts of speech; uses of 一定 as an attributive modifier (一定的关系)

```rule
match: 差不多/VV
match: 差不多/AD
match: 好像/AD
unless: 一定/AD 要
unless: 必须/VV
unless: 也许/AD
```

**Notes:** The rule targets the two main adverbs from the official list: 差不多 and 好像. These appear with POS tags VV or AD depending on function. The unless clauses exclude 一定 and 必须 which are different modal meanings (necessity/certainty rather than approximation/seeming), and 也许 (possibility). The patterns are intentionally simple to avoid false negatives while maintaining precision—any sentence containing 差不多 or 好像 in their typical tags will demonstrate the grammar point.

## 2-20
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Adverbs expressing lateness (才), earliness (就), coincidence (正好), and small quantity (才/就)

**Covers:** 
- 才 expressing lateness after a clock time or age: "Subj. + Time + 才 + Verb" (e.g. 他二十五岁才上大学, 我今天八点才起床)
- 才 in complaints about lateness: 你怎么才来
- 等了 + duration + 才 + Verb (e.g. 我等了两个小时才买到票)
- 才 expressing small quantity: "才 + Number" or "才 + Verb + 了 + Number" (e.g. 她才买了两本书, 你才二十岁, 现在才九点)
- 就 expressing earliness after a clock time or age: "Time + 就 + Verb" (e.g. 她十九岁就结婚了)
- 就 expressing small quantity: "就 + Number + Measure + Noun" (e.g. 他们家就一个孩子)
- 正好 expressing coincidence: "Subj. + 正好 + Verb Phrase" or as a predicate adjective (e.g. 我正好要出去, 这件衣服正好)

**Does not cover:** 
- Causal or "only after" 才 that follows a completed action or a quantity of effort (花了…才, 看了两遍才, 只有…才, 直到…才, 等到…才, 多亏…才, 好(不)容易才)
- Contrastive/emphatic 才 (你才是笨蛋)
- 才 as a noun meaning "talent" (人才, 天才)
- 才 inside compounds (刚才, 才能)

```rule
match: /CD 点|岁/M|NN 半/CD? 才/AD /VV
match: 怎么 才/AD /VV
match: /NT 才/AD /VV
match: 等/VV 了/AS ... 才/AD /VV
match: 才/AD /CD
match: 才/AD /VV /AS? /CD
match: /CD 点|岁/M|NN 半/CD? 就/AD /VV
match: 就/AD /CD /M /NN
match: 正好/AD
match: 正好/VA
unless: 只有|直到|等到|多亏|好容易|好不容易
unless: 好/AD 不? 容易/VA
unless: 花/VV
unless: 才/AD 是/VC
```

**Notes:** 
- 才 only counts as lateness when it directly follows a clock time, age or time noun, or follows 怎么 or 等了; a bare "才 + Verb" is too often the "only after" pattern, so it no longer matches.
- Sentences where 才 follows a quantity of effort (花了八千块才, 看了两遍才, 找了几个月才) are not matched, because they are the "only after" use.
- 等 is no longer in the unless list. 等到 (tagged P) is excluded instead, so 等了…才 can still match.
- 就 as earliness is lim

## 2-21
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** 当 used as a preposition meaning "when", usually in the frame 当 + clause (+ 的时候 / 时), followed by the main clause.
**Covers:** 当 tagged as a preposition (P) that opens a "when" clause, with or without a closing 的时候 or 时. This includes forms with a preceding time word, such as 每天当超市开门的时候. It also covers 当天 + clause + 的时候, because the tokenizer merges 当 and 天 in 当天快黑的时候.
**Does not cover:** 当 as a verb ("to work as"/"to treat as"): 当老师, 当了官, 当成, 当作, 把…当回事. It also excludes 当中 ("among"/"in the midst of"), 当地, 当时, 当年, 当前, 当代, 当场, 当选, 应当, 上当, 正当, 相当, 不敢当, because these are different words or tags.

```rule
match: 当/P
match: 当天/NT ... 的 时候/NN
```

**Notes:** The rule relies on the tagger marking the "when" 当 as P. The counter-examples have 当 as VV, or have the compounds 当作 and 当中 as single tokens, so they are not matched. If false positives appear, restrict the pattern to `当/P ... 时候|时` and add a separate clause-start pattern. The 当天 line may occasionally match "on that day … when" sentences, but it is needed for the labeled example.

## 2-22
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Using 往 (wǎng) as a preposition or directional verb to express movement "towards" a location or direction.
**Covers:** Sentences where 往 appears before a direction/location word and then a verb, indicating the direction of an action. This includes 往 + direction/place + verb (including 往下 + 说/读/看); verb + 往 + place (开往, 飞往, 运往); and the modifier use in noun phrases (e.g., 开往北京的火车).
**Does not cover:** 往往 (wǎngwǎng) used as a frequency adverb meaning "often/usually" (HSK 3-15); multi-character words or time nouns such as 来往, 交往, 往来, 往年, 往后, where 往 is not a separate word.

```rule
match: 往/P|VV /NN|LC|NR /VV
match: 往/P|VV /NN|NR /NN|LC /VV
match: 往/P|VV /NN|NR /NN /LC /VV
match: /VV 往/VV /NN|NR
match: /VV 往/NN /NN
match: 往/P _ /VV
unless: 往往/AD
unless: /NN 往/NN
```

**Notes:** The tagger is inconsistent: 往 is tagged P, VV or occasionally NN, depending on context. The rule therefore accepts 往 as P or VV followed by a direction/place word and then a verb, which covers 一直往前走, 往下说 and 你往前走一百米. For verb-往 compounds (开往北京的, 飞往纽约的, 运往国外的), it accepts a verb followed by 往 (tagged VV or NN) and then a place noun. 往往 is a separate token tagged AD, so the `unless: 往往/AD` line removes the frequency-adverb sentences. Words like 来往 and 交往 are single tokens, so they never match 往 as a standalone word.

## 2-23
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Using 向/朝/往 as a preposition meaning "towards" to indicate the direction or target of an action

**Covers:** 向/朝/往 + direction/person/place + verb (walk towards, look at, apologize to, learn from, report to). Also verb + 向/往 + destination after motion verbs (开往, 飞往, 飞向, 驶向), and 向前 + verb.

**Does not cover:** Adverbial 一向 and 向来 (always); 转向 as a verb compound; 向 as a noun meaning direction or tendency.

```rule
match: 向|朝|往/P|VV _ _? _? /VV
match: 向前/AD /VV
match: 开|飞|发|运|送|寄|带|驶|奔|冲|流|跑|游|走 向|往/P|VV /NN|NR|PN|JJ
match: 驶向|飞向|奔向|冲向|流向|跑向|开往|飞往|驶往|发往|运往|送往|寄往 /VV
unless: 一向/AD
unless: 向来/AD
unless: 转/VV 向/VV
```

**Notes:** The old rule used invalid grouping syntax and required a single word between the preposition and the verb. That failed on 向雷锋同志学习 and 向环保部门要求, and the rule also missed 向 tagged VV. The first line now allows 向/朝/往 tagged P or VV, followed by up to three words and then a verb. Separate lines cover 向前 tagged as an adverb, and the post-verbal 开往/飞往/飞向/驶向 type, which has no following verb. 一向 and 向来 are single tokens and are still excluded explicitly. 转向 is excluded.

## 2-24
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Using 从 (cóng) and 离 (lí) as prepositions meaning "from" in spatial, temporal, or source contexts

**Covers:** 
- 从 + Place + verb (motion/action from a location)
- 从 + Place + 到 + Place (from place to place)
- 从 + Time + 到 + Time (from time to time)
- 从 + Time/Event + 开始/起 (starting from)
- 从 + 那/今 + 以后 (ever since then / from now on)
- 从 + Source (noun/person/abstract) + verb (taking/learning from, seeing from)
- 离 + Time/Event + duration, 离 + Event + 还有 + duration (time distance)
- 离 + Place + 远/近 (distance)

**Does not cover:**
- 从来 meaning "always" or "never" (time adverb)
- 从而 meaning "thereby/thus" (conjunctive adverb)
- 自从 (a separate HSK 3 grammar point)
- 从...来看 (HSK 5-26)
- Adverbial or fixed uses such as 从小, 从中, 从此, 从前, 从不, and the verb 从事, none of which are tagged as the preposition 从/P

```rule
match: 从/P
match: 离/P
unless: 从来
unless: 从而
unless: 自从
unless: 从事
unless: 从/P ... 来看
unless: 从/P ... 来 看
```

**Notes:** 
- Matching on the preposition tag 从/P or 离/P is enough. The fixed adverbial forms are tokenized as separate words (从来, 从小, 从今以后 and so on) or tagged AD, so they never match.
- The earlier rule demanded a particular following word (到, 起, 上 and so on). That missed many valid uses: 从 + noun + verb, 从那以后, and 从 + 到 where 到 is tagged as a verb or preposition.
- The `unless` lines still remove sentences containing 从来, 从而, 自从, 从事 or 从...来看, so they cannot slip in on a stray 从/P.
- The standalone token 从今以后 (tagged NT) is not covered by this rule, because it is not tagged 从/P.

## 2-25
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Using 对 as a preposition meaning "to/towards/regarding" (the target of an action or emotion), or as a verb meaning "to face/point at/match" (对着, 对上, 对不上).

**Covers:** 对 + Object + Verb/Adjective (preposition directing an action or attitude to a target, e.g. 对我笑, 对她说, 对工作很认真, 对你不满意); 对着/对上/对不上 (verb phrases); 对(于) written with the 于 split off; 对 and 跟 as prepositions with 说.

**Does not cover:** 对于 as a single word (HSK 4-14); 对...来说 (HSK 3-40); 对...而言; 对方 (the pronoun "other party"); tag questions 对不对/对吗; 对 as adjective meaning "correct".

```rule
match: 对/P
match: 对/VA 于/P
match: 对/VA|VV 着/AS
match: 对/VA|VV 上/VV
match: 对/VV 不/AD 上/VV
unless: 而言
unless: 来说
unless: 来 说
unless: 对方
unless: 对 不 对
unless: 对 吗
```

**Notes:** The old first line used a literal "P" instead of the tag, so it never matched. Prepositional 对 is always tagged 对/P, and the object may be followed by a verb or an adjective, so the rule now matches 对/P on its own. The 对于 single-word token is a different word and is not matched. The 对/VA 于/P line covers the 对(于) examples where the tokenizer splits the word. The verbal uses (对着, 对上, 对不上) are matched explicitly, since the tagger gives 对 either VA or VV there. The unless lines exclude 来说, 而言, 对方 and the tag questions 对不对 and 对吗.

## 2-26
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** 给 (gěi) as a preposition marking the target/recipient of an action

**Covers:** Subj. + 给 + Target/Recipient + Verb Phrase, where 给 indicates who or what the action is directed at or done for. This includes clause-initial 给 (e.g. 给客人倒茶) and targets of two or three words (e.g. 给小狗洗澡, 给孩子们做饭).

**Does not cover:** 给 as the main verb "to give"; 给 after the verb (verb + 给 + object, e.g. 送给, 嫁给); passive 给 (被/叫/让 + 给, or Receiver + 给 + Doer + Verb + 了, e.g. 这么好的机会给你浪费了); 给 in 把-constructions; 为 + person patterns (a different word, left out for precision).

```rule
match: 给/P _ /VV
match: 给/P _ _ /VV
match: 给/P _ _ _ /VV
unless: /BA ... 给
unless: 被|叫|让 _ 给
unless: /LB|SB ... 给
unless: _ 给/VV
unless: _ 给/MSP
unless: /DEC _ 给/P
unless: 给/P _ /VV /AS $
unless: 给/P _ _ /VV /AS $
```

**Notes:** The rule now matches 给/P at the start of a clause as well as after a subject, and allows 1–3 words between 给 and the verb. Two new unless clauses block the passive "这么好的机会给你浪费了" type. The first blocks a relative-clause 的 (DEC) + noun directly before 给. The second blocks 给 + target + verb + 了 with nothing after the verb, which is the typical passive shape. This also drops rare active sentences such as 我给你做了。, which is an acceptable loss for precision. The 为 examples in the labeled data are deliberately not matched, since loosening to 为 would let in many unrelated sentences.

## 2-27
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Expressing distance (in space or time) between two places or points using 离 (lí)
**Covers:** 离 tagged as P, followed by a second place or time point, then 近/远 (including 多远, 有点儿远, and a trailing 吗). It also covers 离 + point + 有 + number, as in 现在离放假有一个星期的时间.
**Does not cover:** Other uses of 离 as a verb meaning "leave" or "depart" (离开, 撤离, 远离, 脱离), compound verbs (离婚), and separable verb phrases (离不开). These are not tagged 离/P.

```rule
match: 离/P ... 近|远/VA
match: 离/P _ 有/VE CD
match: 离/P _ _ 有/VE CD
unless: 离/VV
```

**Notes:** The original rule did not match any of the labelled examples. The parenthesised alternations such as (近|远) are not valid in this syntax, and the leading `_` forced an extra word before 离. The fix drops the parentheses and writes the alternation as 近|远/VA. Requiring 离 to be tagged P keeps out the verb uses (离开, 远离, 离婚 and so on), so most of the old 离开/撤离/远离 exclusions are no longer needed. 多远 is split into 多/AD and 远/VA, so 远/VA covers it. The pattern for 有 plus a number covers the time-distance example (离放假有一个星期).

## 2-28
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** The preposition 为 (wèi), "for", introduces the person or party an action is done for: 为 + party + Verb/Adj. The labeled set also includes the parallel 给 + recipient + verb pattern.

**Covers:** 为 tagged as a preposition before a person or thing and then a verb or adjective. Examples: 为你高兴, 为公司提供保障, 是为你准备的, 为美国政府工作, 为大家干杯. It also covers 给 tagged as a preposition ("for/to someone", as in 给我打电话, 给他道歉), because the course's labeled examples include these.

**Does not cover:**
- 为了 (purpose), which the tagger treats as a separate token.
- 因为 (because), also a separate token.
- 作为.
- 以…为… ("take X as Y"), where 为 is tagged VV.
- 成为, 分为, 称为, 以为, 为什么, 为何, 为此, 为期 and 为难, which are all single tokens or tagged differently.
- 为 tagged VC or VV, as in 评选为模范.
- 给 as a main verb ("give"), which is tagged VV.

```rule
match: 为/P
match: 给/P
unless: 以 ... 为/P
```

**Notes:**
- The rule relies on the tagger giving bare 为 the tag P only in the "for" sense. The labeled counter-examples show 以…为 getting VV, but the extra `unless` line guards against a mis-tag.
- The 给/P line is there only because the labeled positives include 给 sentences. Delete it if the point should cover 为 alone.
- Bare 为 tagged VV or VC (e.g. 为荣, 评选为) is deliberately not matched.

## 2-29
<!-- written by claude-haiku-4-5 on 2026-11-09; edit freely -->
**Point:** Using 或/或者 to express "or" in statements (offering alternatives)
**Covers:** Sentences where 或 or 或者 connects two alternatives in a statement, typically offering choices or possibilities (not yes/no questions)
**Does not cover:** Sentences using 或许 (perhaps/maybe); sentences using 或是 (5-45); sentences using 或A或B pattern (6-25); sentences where 或/或者 appears only in titles or metalinguistic contexts

```rule
match: ... 或|或者/CC ...
unless: 或许/AD
```

**Notes:** 
- The rule is intentionally simple: it matches any clause containing 或 or 或者 tagged as CC (coordinating conjunction), which is how the tagger marks this specific usage.
- The 除非: line excludes 或许 (meaning "perhaps"), which is an adverb and uses a different tag (AD).
- Sentences can have the point even without 都 following—while many examples show 都, it's not required by the grammar point itself.
- The pattern matches within a single clause, so multi-clause sentences will trigger if any clause has the pattern.
- Counter-example "去或是不去" is excluded because 或是 is HSK 5-44, not HSK 2-29; tagging will show 或是/CC as a single token, not matching 或|或者.
- Counter-example "或早或晚" follows the 或A或B pattern (HSK 6-25), but this rule will match it. If precision is critical, add `unless: ^或|或者 ... 或|或者 $` to exclude that pattern, but the examples provided suggest including it is acceptable at HSK 2 level.

## 2-30
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Conjunctions and adverbs that join clauses to express conditions, contrasts and progression (只要…就, 如果…就/那, 的话, 但是/可是/不过, 虽然…但是, 不但…而且/也/还)

**Covers:**
- Conditionals with 如果…就/那(么), 只要…就, and 如果…的话 or a bare 的话
- Contrasts with 但/但是, 可是, and the softer 不过 at the start of a clause
- Concessions with 虽然…但是/可是
- Additive "not only…but also" with 不但/不仅/不只 plus 而且/也/还
- 那/那么 opening the result clause after a condition
- Implied conditions such as 喜欢就拿走吧 and 有事给我打电话

**Does not cover:**
- 尽管…还是 (despite…still)
- 不但…更 and 不但…甚至连…也 (escalation)
- 却 as a contrastive adverb
- 并且 (moreover/and), including 不但…并且
- 不但不/不但没有…反而 (HSK 6)
- 不过是 (just/merely), where 不过 is not a conjunction

```rule
match: 只要/CS
match: 如果/CS
match: 虽然/CS
match: 不但/AD
match: 不仅/AD
match: 不/AD 只/VV
match: 的话/SP
match: ^ 不过/AD
match: ^ 不过/CC
match: ^ 但是/AD
match: ^ 但是/CC
match: ^ 可是/AD
match: ^ 可是/CC
match: ^ 但/AD
match: ^ 但/CC
match: ^ 而且/AD
match: ^ 而且/CC
match: ^ 那/AD
match: ^ 那么/AD
match: ^ 喜欢/VV 就/AD
match: ^ 有/VE 事|空 给/P
match: ^ 有/VE 事|空 来/VV
unless: 甚至
unless: 更/AD
unless: 尽管
unless: 却/AD
unless: 并且
unless: 反而
unless: 不过 是
```

**Notes:**
- The original rule never matched because a pattern only looks inside one clause. The connectors sit in different clauses, so each one is now matched by itself, anchored with ^ where it must open a clause.
- 不过, 但是, 可是 and 而且 are often tagged AD rather than CC, so both tags are listed.
- 不过, 但, 可是 and 那 are restricted to clause-initial position so that mid-clause uses (不过是…, 他可是我老板, 那个) do not match.
- The unless lines remove the neighbouring points: 甚至连, 更, 尽管, 却, 并且, 反而 and 不过是.
- The implied-condition patterns (喜欢就…, 有事/有空…) are deliberately narrow to avoid false positives.

## 2-31
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Degree complement (得) to modify how a verb is done or to what degree an adjective applies

**Covers:** Verb + 得 + (adverb) + adjective (跑得快, 吃得太饱, 说得很好, 说得不太好); Verb + 得 + 怎么样 / (还)可以; Adjective + 极了 / 死了 / 坏了 (好极了, 可爱死了, 高兴坏了); repeated-verb and topic-fronted structures (你写字写得很漂亮, 你的字写得很漂亮)

**Does not cover:** Modal 得 (have to); 值得 (worth); 不得不 (must); 显得 (appear); potential complements, including the question form with 吗 (看得清楚吗); complement 着; comparison 得多; 的 / 地 structures

```rule
match: /VV /DER /AD? /AD? /VA
match: /VV /DER 怎么样/VV
match: /VV /DER /AD? 可以/VV
match: /VA /DER /AD|VA
match: /VA 极了|极/AD
match: /VA 死 了
match: 急|高兴 坏 了
unless: 值得/VV
unless: 不得不/AD
unless: 显得/VV
unless: 得/VV
unless: /DER 着/VV
unless: /DER 多/VA
unless: /DER ... 吗/SP
```

**Notes:** 
- The first pattern allows up to two adverbs between 得 and the adjective, so 画得很好, 学得挺快的, 吃得太饱了 and 打得很不错 all match.
- Verbs tagged VV as the complement (怎么样, 可以) are matched by their own patterns instead of a blanket /VV /DER /VV rule.
- The modal 得 (tagged VV, as in 你得穿得正式一点) blocks the whole sentence.
- A 吗 question after 得 is treated as a potential complement and blocked (看得清楚吗, 做得好吗). This also drops genuine degree questions like 谈得顺利吗, which is the price of favouring precision.
- Adjective + 极了 / 死了 / 坏了 are included because they are labeled 2-31. The 坏了 pattern is limited to 急 and 高兴 so resultative uses like 弄坏了 are not caught.
- The 的 / 地 examples carry no 得, so they are left unmatched. Matching them would let in far too many unrelated sentences.

## 2-33
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Aspect particle 着 (zhe) indicating continuous action/state, and alternative existential sentences with 着 or 是

**Covers:** 
- Verb + 着 to show an action/state that continues or persists (e.g., 坐着 "sitting", 开着 "is on")
- Verb1 + 着 + Verb2 where the first verb describes a state accompanying an action (e.g., 站着吃饭 "eat standing up")
- Subj. + Verb + 着 + Object in commands/descriptions (e.g., 看着行李 "watch the luggage")
- Place + Verb + 着 + NP for existential meaning (e.g., 桌子上放着书 "there is a book on the table")
- Place + 是 + NP for existential meaning, including with 都 (e.g., 袋子里是我的午饭 "my lunch is in the bag", 墙上都是照片)

**Does not cover:**
- 来着 (laizhe) meaning "was [formerly]" - counter-example: 我说什么来着
- Resultative complement 着 meaning "succeed in" (睡着 "fell asleep", 找着 "found", 买着, 见着) - counter-examples: 宝宝刚睡着, 你的手机找着了吗, 我终于见着你了
- 得着/不着 resultative complement - counter-example: 买不着, 找得着
- 随着 (suizhe) meaning "along with" - counter-example: 随着风摇动
- 沿着 (yánzhe) meaning "along" - counter-example: 沿着大道走
- Perfective aspect 了 - only match 着/AS tag, not 了/AS

```rule
match: /VV 着/AS
match: /LC 都/AD? 是/VC
unless: 随着/P
unless: 沿/P
unless: /P 着/AS
unless: 来/VV 着/AS
unless: /VV 不/AD 着/VV
unless: /VV 得/DER 着/VV
unless: 睡|找|买|见|猜|碰|遇|摸|够|赶|撞|抓/VV 着/AS
```

**Notes:** 
- The rule prioritizes precision by excluding prepositions (随着, 沿着, and general /P 着/AS patterns) which would catch "along with" uses.
- It excludes 来着 by matching the specific pattern "来/VV 着/AS".
- It excludes resultative complements by matching /VV 不/AD 着/VV and /VV 得/DER 着/VV patterns, and by listing verbs that almost always take 着 as a resultative complement (睡着, 找着, 买着, 见着, 猜着, 碰着, 遇着, 摸着, 够着, 赶着, 撞着, 抓着). The tagger labels these 着 as AS, the same as the continuous 着, so the verb list is the only way to tell them apart.
- The two match patterns cover: (1) the aspect particle case (VV 着), and (2) existential 是 sentences (LC 是, with optional 都 between, as in 墙上都是…).
- Many unlabeled sentences show 着 used with transitive verbs describing states (摇着, 穿着, 抱着) which should match /VV 着/AS and are part of this grammar point.

## 2-34
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Sentence-final modal particles 吧 and 啊. 吧 softens a statement, makes a suggestion, concedes a point, or sets out two options (X吧…，Y吧…). 啊 adds exclamation, urgency, or a list-like "you know" feel.
**Covers:** Any clause that contains the particle 吧 or 啊 tagged SP. This includes:
- suggestions and commands: 我们走吧
- softened statements and concessions: 太贵了吧
- tag-style questions: 您是老师吧？
- the double-吧 "options" pattern
- exclamations: 今天真冷啊！
- 啊 after a question or after each item in a list: 椅子啊，桌子啊

**Does not cover:**
- 吧 as a noun, as in 网吧, because it is not tagged SP.
- The challenging "Adj + 什么 (+ Adj) + 啊" pattern, as in 累什么累啊 and 胖什么胖啊. This is a separate point (3-81 / 4-22). The rule excludes it by pattern, and also when 啊 is tagged UH or IJ.
- 还是……吧 (2-45).
- X就X吧 (3-80 / 6-57).
- 你X你的吧 (4-74).

```rule
match: 吧/SP
match: 啊/SP
unless: /VA 什么 啊|呀
unless: /VA|VV 什么/PN|DT /VA|VV 啊|呀
unless: 难过|担心|紧张|害怕|生气|着急|郁闷|伤心|激动|发愁|客气|骄傲|怕 什么
unless: 还是 ... 吧/SP
unless: /VV 就 /VV 吧/SP
unless: 你 /VV 你 的 吧/SP
```

**Notes:**
- The rule relies on the tag SP for 吧 and 啊. When 啊 is tagged UH or IJ it is ignored, which is how some of the 什么-challenge counter-examples are already excluded.
- The 什么 exclusions work on clause patterns. The counter-example 难过什么啊 has 难过 tagged VV, so it can't be separated from the positive 看什么啊？ by tag alone. That is why 难过 and similar emotion words are listed by name. Extend that list if more such sentences appear.
- The 还是…吧 exclusion only applies when 还是 comes before 吧 in the same clause. So 不见面吧，还是会想他 is still matched, because 还是 sits in a different clause.
- The 就 exclusion could occasionally drop a legitimate sentence like 你去就去吧. That is acceptable, since the rule favours precision.

## 2-35
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Using 的话 to express a conditional "if" (usually with 如果/要是/假如 before it, or a negative condition like 不…的话), and using 等 as "etc." after a list of items.

**Covers:**
- Conditional clauses framed as 如果/要是/假如/假设/若是 … 的话, with or without 就 in the next clause
- Negative conditions of the form 不 … 的话, e.g. 不想去的话就别去了
- 等 as an "et cetera" marker after a list of items

**Does not cover:**
- Topic marking with 的话, such as 北京的话，秋天去最好 or 买衣服的话，还是要去实体店试试的, where 的话 introduces a topic and no "if" is meant
- 的话 as a noun meaning "words/speech" (tagged DEG, not SP)
- Implied conditionals with no 的话, such as 喜欢就拿走吧 (too hard to separate from ordinary 就 sentences, so they are missed on purpose)
- Bare verb-phrase + 的话 with no 如果-type word and no negation, which is too easily a topic

```rule
match: 如果|要是|假如|假设|假使|若是|如若 ... 的话/SP
match: 不|没|没有 ... 的话/SP
match: 等/ETC
```

**Notes:** The rule now requires explicit evidence of a condition before 的话: a conditional conjunction in the same clause, or a negation such as 不/没. Topic-marking 的话 almost never has either, so the topic counter-examples are no longer matched. Unmarked cases like 你要来的话 are given up for the sake of precision, because they look the same as topic uses. 等 as an etc. marker is kept as before.

## 2-43
<!-- written by claude-haiku-4-5 on 2026-11-09; edit freely -->
**Point:** Using 什么的 to end a non-exhaustive list with "and so on"
**Covers:** Sentences where 什么的 appears after a noun or list of nouns to indicate an open-ended category of similar items
**Does not cover:** Uses of 什么 and 的 that are not together as the phrase 什么的 at the end of a list; uses where 什么的 is not functioning as a list-ender

```rule
match: _ 什么/PN 的/DEG|DEC $
unless: 什么/PN 的/DEG|DEC _ 什么/PN 的/DEG|DEC
```

**Notes:** 
- The pattern requires 什么的 at the end of a clause (before punctuation or end of sentence)
- The pattern `_ 什么/PN 的/DEG|DEC $` matches any word followed by 什么的 at clause-end, capturing the core structure
- Both DEG and DEC tags are included since the tagger uses both for 的 in this construction
- The `unless` clause prevents false matches where 什么的 appears twice (which would indicate a different grammatical construction)
- The rule correctly identifies the pattern across various sentence structures: after single nouns (练习什么的), after noun lists with punctuation separators (水果、面包什么的), and before continuation (什么的都要自己做)

## 2-44
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** 越来越 + adjective/verb expresses "more and more": a quality or state increasing over time (e.g. 天气越来越热了).
**Covers:** The simple fixed form 越 来 越 followed by a predicate (adjective or verb, possibly with 不 or an object), with or without 了. Includes the tagger variant where 来 is tagged VV. Also covers 得 + 越来越 + adjective (你说得越来越好了).
**Does not cover:** The two-part correlative 越A越B (越多越好, 越吃越胖), which has no 来. Also excluded are 越来越 at the end of a clause with nothing following it, since there is no predicate to demonstrate. A 越来越 split across punctuation is excluded too.

```rule
match: 越 来/AD|VV 越 /VA|VV|AD
unless: 越 来/AD|VV 越 $
unless: 越 来/AD|VV 越 /AD $
```

**Notes:** The tokenizer splits 越来越 into three tokens, 越/AD 来/AD 越/AD. Occasionally 来 is tagged VV, so the rule allows both tags. The rule requires a predicate (VA, VV, or an AD such as 不) right after the second 越. Because patterns are matched within one clause, a bare 越来越 before punctuation is rejected by the `unless` lines. Simple patterns like these are precise because 越 来 越 as three consecutive tokens is essentially unambiguous. If a counter-example turns up, such as 越来越 used as a noun phrase, tighten the final slot.

## 2-45
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Expressing "had better" with 还是...吧 (háishì...ba)
**Covers:** Suggesting a course of action after deliberation, with 还是 as an adverb meaning "had better" or "it would be better to", usually ending the clause with 吧. Also covers negative suggestions (还是 + 别/不要 + verb) where 吧 may be omitted.
**Does not cover:** 还是 as a coordinating conjunction offering choices (Option A 还是 Option B?). Also excluded are 还是 meaning "still" or "nevertheless" (他还是买了, 尽管…还是…, 了又…还是), 呢 choice questions (打游戏呢，还是泡吧呢), and other uses of 吧 without 还是.

```rule
match: 还是/AD ... 吧/SP $
match: ^ 还是/CC|AD 不要|别/AD
match: 还是/AD 别/AD
unless: 尽管/CS
unless: 了/AS 又/AD|CC
unless: 又/AD|CC ... 还是/AD
unless: 但是/CC|AD ... 还是/AD
unless: 可是/AD ... 还是/AD
unless: /CS ... 还是/AD
unless: 呢/SP ... 还是/AD
unless: 还是/AD ... 呢/SP
unless: 不要/AD ... 还是/AD
unless: 想/VV ... 还是/AD ... /PN
```

**Notes:** The main pattern requires the clause to end in 吧 after 还是 used as an adverb, e.g. 还是让她进来吧 and 你还是坐地铁吧. This keeps out "still" sentences such as 他还是买了 and 她还是喝. Without 吧, only negative suggestions with 别 or 不要 are accepted. The tagger sometimes labels clause-initial 还是 as CC, as in 还是不要再问了, so that case is allowed only when 不要/别 follows directly. The unless lines exclude concessive, contrastive and repeated-action contexts and 呢 choice questions.

## 2-46
<!-- written by claude-haiku-4-5 on 2026-11-09; edit freely -->
**Point:** 又⋯⋯又⋯⋯ (both A and B)
**Covers:** Two adjectives or stative verbs connected by 又, expressing that a noun/subject has two qualities simultaneously (both positive or both negative)
**Does not cover:** "又" used for repetition, negation emphasis, or "again"; uses where 又 appears only once; uses where the second 又 is not immediately followed by an adjective/stative predicate

```rule
match: _ 又/AD|CC /VA|VV 又/AD|CC /VA|VV
match: _ 又/AD|CC _ 又/AD|CC /VA|VV
unless: /VV 了/AS 又/AD|CC /VV
unless: 又/AD|CC 不/AD
unless: 又/AD|CC 没/AD
unless: 又/AD|CC 能/VV
unless: 又/AD|CC 可以/VV
unless: 又/AD|CC 要/VV
unless: /AS 又/AD|CC /VV
unless: /AS 又/CC /VV
unless: _ 又/AD|CC /VV 了/SP
```

**Notes:** The pattern allows for an optional modifier (like 不 for negation or 难 for "difficult to") between the two 又s. The unless clauses exclude: repeated actions with 了 + 又; negation emphasis (又 + 不/没); modal verbs (又能/又可以/又要); and the "again" sense (evident from /AS or /SP markers). The rule matches both cases where 又 is tagged AD or CC, as tagging varies in the examples.

## 2-47
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** 以前/以后 (and the split form 以/前, 以/后) mark time before or after a time point or an action, or refer to "before" or "in the future" in general.

**Covers:**
- Time or verb phrase + 以前/以后, e.g. 睡觉以前，… / 吃完午饭以后，… / 几年以后，… / 在来中国以前，….
- Sentence-initial 以前/以后 (以后你想来中国吗？).
- 以前/以后 directly after the subject (爸爸以前很喜欢抽烟 / 我以后不喝酒了).
- The tagger's split 以/P + 前/后, as in 两年以前 and 几个月以前.

**Does not cover:**
- Sentences with no 以前/以后 token. This leaves out the bare short forms 前/后 (e.g. 饭前, 后年), which are too ambiguous to detect safely.
- Sentences that also contain 后来. These are the "then, later" sequencing pattern (他以前是厨师，后来当了老板), which is a different point.
- 在内, 在于, 早在, 在…上/下, 在 + place, and other 在 or 前/后 uses, because they never contain 以前/以后.

```rule
match: 以前
match: 以后
match: 以 前
match: 以 后
unless: 后来
```

**Notes:**
- Matching is by word, regardless of tag, because the tagger gives 以前/以后 many tags (NT, NN, AD, LC).
- Apart from 后来, 以前/以后 almost always express this point, so there are no other `unless:` lines.
- 后来 is excluded because "X以前…，后来…" contrasts a past state with a later one. Labeled examples treat it as the 后来 sequencing point, not 2-47. This also drops the rare sentence that has both patterns, which is acceptable because precision comes first.
- Modifier uses like 以前的同学 would also match. They are rare and still teach the meaning of 以前. Add `unless: 以前 的` or `unless: 以后 的` if they should be excluded.

## 2-49
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Result complements (结果补语): a verb followed directly by 错/懂/干净/好/清楚/完 (also 饱/坏/晚/破), showing the result of the action.

**Covers:**
- Verb + result complement, with or without 了: 写错了, 没看懂, 做完, 吃好了, 听清楚, 玩坏了, 来晚了, 吃饱
- Negation with 没: 我没吃饱, 他还没想好
- Sentences where the object is moved to the front or into a 把 phrase, as long as the verb and complement stay adjacent

**Does not cover:**
- 会/能 as modals, or 会 in any other use (你会来吗？, 他会说中文)
- 好 as an adjective, or in 好像, 好吃, 好看, 好玩, 好写, 好懂
- 好 meaning "in order to"
- 最好, 只好, 正好, 还好, 好(不)容易
- 好 in tag questions (好吗？)
- 不知道 + verb phrase + 好
- 觉得好 and similar "think it's good" patterns
- 到/见/走/掉 complements, which this rule leaves out for precision

```rule
match: /VV 错|懂|干净|清楚|饱|坏|晚|破|完|好
unless: 会/VV
unless: 能/VV
unless: 还/AD 好/VA
unless: 好/VA 容易/VA
unless: 好容易/AD
unless: 好不容易/AD
unless: 好不容易
unless: 好像/AD
unless: 最好/AD
unless: 只好/AD
unless: 正好/AD
unless: 还好/AD
unless: 不/AD 知道/VV ... 好/VA
unless: 不/AD 知道/VV ... 好
unless: 觉得|认为|希望|祝|感觉 好
unless: 好/AD 懂|写|找|用|学|做|看|吃
unless: 难/AD /VV
unless: 好/AD /VV
```

**Notes:** The rule matches only a verb immediately followed by one of the listed complement words. The old rule matched any two adjacent verbs, which let in every 会 + verb sentence. Any sentence containing 会 or 能 as a verb is now excluded, and so are the 好 constructions (还好, 好容易, 不知道…好, 觉得好). The rule favors precision over recall, so it misses complements that the tagger fuses into a single word, such as 看懂 if tokenized as one word.

## 2-51
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Degree (state) complement with 得: Verb + 得 + Adjective or degree phrase, describing how an action is done. Also covers 极了, 死了 and 坏了 after an adjective, to express degree.

**Covers:** Verb + 得 + adjective, with or without an adverb such as 很, 太, 挺 or 还. Also 得 + 怎么样 and 得 + 还可以. Also adjective + 极了 / 死了 / 坏了, and 急坏了.

**Does not cover:** Potential complements asked as yes/no questions with 吗 (看得清楚吗). Modal 得 meaning "must". 不得不, 值得, 显得, 害得, 怪不得, 难怪. Adjective + 得很 or 得不得了. The 得倒 pattern.

```rule
match: /VV /DER /AD? /VA
match: /VV /VV /DER /AD? /VA
match: /VV /DER 怎么样
match: /VV /DER 还 可以
match: /VA 极了/AD
match: /VA 极 了
match: /VA 死 了
match: /VA 坏 了
match: 急 坏 了
unless: 不得不
unless: 值得
unless: 显得
unless: 害
unless: 怪不得
unless: 难怪
unless: /DER 倒
unless: /DER 不得了
unless: /DER 得很
unless: /DER 得不得了
unless: /DER /AD? /VA 吗
unless: 得/VV
```

**Notes:** The core pattern is VV + 得 + (AD) + VA, including the verb-repeated form (他英语说得…). I added special cases for 怎么样 and 还可以, since these are tagged VV and the VA pattern misses them. I also added degree endings after an adjective (好极了, 臭死了, 高兴坏了, 急坏了). Rather than a generic VV + 坏, these use 急坏了 and VA + 坏 only, so resultatives like 打坏了 are not matched. For precision, any sentence with modal 得 (tagged VV) is excluded, which removes 你得穿得… and 得去看医生. Questions of the form …得+adj+吗 are also excluded, because they are usually potential complements (看得清楚吗, 做得好吗). The cost is that genuine cases like 谈得顺利吗 are missed. The old unless lines on 得 followed by a VV were dropped, because they blocked 怎么样 and the VA matches already require an adjective after 得.

## 2-55
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Using 有 to express comparisons ("as [adjective] as"), typically in questions, plus 有 with a measured amount ("as tall as / about N years old").
**Covers:** 
- A 有 B Adj. 吗 (他有我帅吗)
- A 有 B Verb 得 Adj. 吗 (他有我做得好吗)
- A 有 (B) 这么/那么 Adj. 吗 (她有那么漂亮吗)
- A 有 + number + measure + 高 (他有一米八高)
- A 有 + number + 多 + 岁 (他有三十多岁)

**Does not cover:** Possession (我有钱), existence (这里有人), 有点/有一点 (a little), 有的 (some), 只有 (only if), 还有 (also), 有关 (related to), Taiwanese 有, negation 没有, or when 有 simply means "to have."

```rule
match: 有/VE /PN|NN|NR /VA 吗/SP
match: 有/VE /PN|NN|NR /PN|NN|NR /PN|NN|NR? /VA 吗/SP
match: 有/VE /PN|NN|NR? /VV 得/DER /VA 吗/SP
match: 有/VE /PN|NN|NR? 这么|那么/AD /VA|VV /NN? 吗/SP
match: 有/VE /NR 吗/SP
match: 有/VE /CD /M /CD? 高/NN
match: 有/VE /CD /M 高/VA
match: 有/VE /CD 多/CD 岁/M
unless: 有的/PN
unless: 没有/VE
unless: 只有/AD
unless: 还/AD 有/VE
unless: 有关/VV
unless: 有点/AD
unless: 有一点/AD
```

**Notes:** The rule matches 有 followed by a comparison standard B and then an adjective, or a verb with 得 and an adjective, or 这么/那么 plus an adjective, usually ending in 吗. It also accepts the 有 + measurement pattern (有一米八高, 有三十多岁). The 有 + proper noun + 吗 line catches cases where the tagger merges B and the adjective (他有姚明高吗). Possession, existence, 有点, 有的, 只有, 还有, 有关 and 没有 are excluded.

## 2-58
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Expressing comparisons with 比 (bǐ), including basic comparisons, "more" with 更/很多/得多/多了, and age differences with 大/小 + number + 岁

**Covers:**
- Basic comparisons: Noun1 + 比 + Noun2 + Adjective (e.g., 小李比小张高)
- Comparisons with 更: Noun1 + 比 + Noun2 + 更 + Adjective (e.g., 小李比小张更高)
- "Much more" comparisons: Noun1 + 比 + Noun2 + Adjective + 很多/得多/多了 (e.g., 拼音比汉字容易很多)
- "A bit more": 比 + Noun2 + Adjective + 一些/一点 (e.g., 房间外边比里边凉快一些)
- Age differences: A + 比 + B + 大/小 + Number + 岁 (e.g., 姐姐比我大两岁)
- Standalone "much more": Subject + Adjective + 多了 with implied comparison (e.g., 我的感冒好多了)
- Age questions without 比: 多大年纪/岁数, 谁大/谁最小, 哪年的
- Same age: A + 和/跟 + B + 一样/差不多 + 大 (e.g., 他女朋友和他女儿一样大)

**Does not cover:**
- 比较 used as an adverb meaning "rather/quite" (e.g., 这个问题比较简单)
- 不比 negation patterns (e.g., 你不比我聪明)
- 比起...来 patterns (e.g., 比起你来，我还差得远呢)
- 相比 patterns (e.g., 相比网络广告，平面广告贵很多)
- 跟...比 patterns (e.g., 跟上海比，我老家的物价很低)
- 比 as a verb meaning "to compete/compare" or in compound words like 比赛, 对比
- 比如 meaning "for example"

```rule
match: 比/P ... /VA $
match: 比/P ... /VA 吗/SP
match: 比/P ... /VA 一些|一点/AD
match: 比/P ... 更/AD
match: 比/P ... /VA 很/AD 多/AD
match: 比/P ... /VA 得/DER 多/VA
match: 比/P ... 多/AD 了/SP
match: 比/P ... /VA /CD 岁/M
match: 比/P ... 大|小/VA /CD
match: /VA 多/AD 了/SP
match: /VA /CD 岁/M 了/SP
match: /CD 岁/M 了/SP
match: 和|跟/CC|P ... 一样|差不多/AD 大/VA
match: 多/AD 大/JJ|VA 年纪|岁数/NN
match: 哪/DT 年/M 的/DEG
match: 谁/PN 大|小/VA
match: 谁/PN 最/AD 大|小/VA

unless: 比较/AD
unless: 不/AD 比/VV
unless: 不/AD 比/P
unless: 比/VV 起/VV
unless: 比/P 起/VV
unless: 相比/VV
unless: 跟/P _ 比/VV
unless: 和/P _ 相比/VV
unless: 与/P _ 相比/VV
unless: 比/VV 赛/NN
unless: 对比/VV
unless: 比如/AD
unless: 比/VV 一/CD
```

**Notes:** 比 tagged P (preposition) is required for the comparison patterns, with "..." allowing multi-word nouns between 比 and the adjective. Plain 比/P ... VA is accepted only when the adjective ends the clause or is followed by 吗 or 一些/一点. 不比, 比较, 比起, 相比 and 跟...比 are excluded by the unless lines. The 谁 and 一样 patterns are limited to 大/小 to keep them within the age sense.

## 2-59
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Basic comparisons using 跟/和 + Noun 2 + 一样 (or 相同) to say that two things are the same or different

**Covers:** Sentences with the structure Noun 1 + 跟/和 + Noun 2 + 一样 (+ optional adjective), including negations with 不一样 and the synonym 相同 (跟我相同).

**Does not cover:** Comparisons using 像 instead of 跟/和. These follow the separate 像…一样 pattern, so any sentence containing 像 is excluded.

```rule
match: 跟|和/P ... 一样
match: 跟|和/CC ... 一样
match: 跟|和/P ... 相同
match: 跟|和/CC ... 相同

unless: 像
```

**Notes:** The old rule required 一样 to follow the preposition directly, or to have only one word between them. Real sentences put a whole noun phrase (他妈妈, 我的头发) between 跟/和 and 一样, so most of them were missed. The new rule allows any material between 跟/和 and 一样 within the same clause. It accepts 跟/和 tagged either P or CC, because the tagger may label them either way. It does not depend on how 一样 is tagged (VA or AD), so it covers 一样, 不一样 and 一样 + adjective. It also covers 相同, as in 他的想法跟我相同. Requiring 跟/和 keeps out other uses of 一样. The unless line removes 像…一样 sentences, which belong to a different point.

## 2-66
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
<!-- revised: patterns now stay within one clause, since a pattern cannot span a clause boundary -->
**Point:** Conditional "if… then…" sentences. The condition is marked by 如果 or 要不是, by a clause-final 的话, or left implicit. The result clause is often introduced by 就/那/那么.

**Covers:** Sentences whose condition clause has 如果, 要不是 or 的话. Also the informal 你要再说 / 他要不听 type, 要我说, the "V/Adj + 就" type (喜欢就拿走吧), and the short "有事/有空 + request" type.

**Does not cover:** 如果/就/那 used only as plain conjunctions or adverbs, and sentences with only a condition or only a result. Sentences with 要是 or 假如 are left to 3-69 and 4-51, so they are excluded.

```rule
match: 如果/CS
match: 要不是
match: 的话/SP
match: ^ /PN 要不/AD /VV
match: ^ /PN 要/VV 再说/VV
match: ^ 要/VV 我/PN 说/VV
match: ^ 喜欢/VV 就/AD
match: ^ 有/VE 事|空/NN 给/P
match: ^ 有/VE 事|空/NN 来/VV
unless: 要是
unless: 假如
```

**Notes:** The earlier rule used cross-clause patterns with ‖, which can never match because a pattern works inside a single clause. The marker in the condition clause is therefore matched directly. Plain 如果, 要不是 and 的话 are reliable signals of an "if" clause. Informal 要 and implicit conditionals are matched only in narrow, anchored forms to protect precision. Sentences containing 要是 or 假如 are excluded because they belong to other HSK points. Bare two-clause sentences such as 明天下雨，我们在家休息 are not matched, because nothing within one clause marks them as conditional.

## 2-67
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Conditional complex sentence 只要……，就……: "as long as / whenever A, then B". A is a sufficient condition for B.

**Covers:** 只要 (tagged CS) opens the condition clause, and 就 (tagged AD) introduces the result, usually in the following clause. Examples: 只要你努力学习，就会有进步。 只要下雨，她就会心情不好。 只要有你在，我就放心了。

**Does not cover:** 就 uses with no 只要 before them: 如果/要是/假如/既然……就, 一……就, 再不……就, 就要……了, 就是/就算, 就 as a preposition (就……问题), 就 meaning "only", and X就X patterns. Sentences with 只要 but no 就 are also excluded, for example 只要 used as a plain "only need" verb phrase, or 只有……才……. The rule is also meant to skip 只要 sentences where 就 is only a fixed 就是/就算.

```rule
match: 只要/CS
unless: 就/P
unless: 就是|就算|就要
unless: 一/AD|CD ... 就/AD
unless: 再/AD 不/AD
unless: 只有
```

**Notes:** All 12 labeled positives have 只要 in one clause and 就 in the next. Patterns cannot span clauses, and the earlier lines (including the "‖" one) never matched them, so I dropped them. The rule now matches any sentence containing 只要 tagged CS. The CS tag keeps out the verb use ("only want/need"). A sentence with 只要 and no 就 (for example one using 都 or 也 as the result marker) would still match, because the syntax cannot require 就 in a later clause. This is a known, rare precision risk. The unless lines drop sentences containing the 就 uses of other points: 就 as a preposition, 就是/就算/就要, 一……就, 再不, and 只有. They also drop a few valid sentences, such as 只要……就要……, which is acceptable under the precision-first goal.

## 2-69
<!-- written by claude-haiku-4-5 on 2026-11-09; edit freely -->
**Point:** Events in quick succession with "yi... jiu..." — as soon as one thing happens, another happens immediately
**Covers:** Sentences with 一 + Event1, then 就 + Event2 in the same or different clauses, showing rapid succession or cause-result
**Does not cover:** 一 used as number/measure word (一个, 一些, 一下 as measure); 一直/一时/一再/一旦/一起/一方面 and other fixed adverbial expressions; verb reduplication or other patterns

```rule
match: 一/AD ... 就/AD
unless: 一/CD
unless: 一再/AD
unless: 一直/AD
unless: 一时/AD
unless: 一旦/CS
unless: 一起/AD
unless: 一/CD _/M
unless: 一/CD 点/M
unless: 一/CD 些/CD
unless: 一/CD 方面/NN
unless: 一天/M 一天/M
unless: 一 _/M 一 _/M
unless: 一/CD _/M 也/AD
unless: 一/CD _/M 都/AD
```

**Notes:** The core pattern is 一/AD (adverbial) followed eventually by 就/AD. We exclude 一/CD (numeral) to avoid "一个, 一些" and similar measure constructions. We exclude fixed adverbials like 一再, 一直, 一时, 一旦, 一起. We also exclude patterns like "一点...也/都不", "一...一..." reduplication, and "一方面" which are separate grammar points. The pattern matches across multiple words between 一 and 就 (within a clause).

## 2-74
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Emphasis with 就 (jiù) placed before the predicate to add emphatic, intense feeling
**Covers:** 就是 + Noun (identifying); 就 + Verb / 就是 + Verb (emphasizing the predicate or a reason); 就是 + 不 + Verb (emphatic negation); 就在 + place (emphatic location)
**Does not cover:** Temporal or sequential uses (about to, as soon as); conditional uses (if-then, only if); scope or topic marking; small quantity (only); indifference (X就X); quick succession; hypothetical concession; 不是……就是……; and other non-emphatic 就 constructions

```rule
match: /PN|DT|NN|NR 就/AD 是/VC
match: /PN|NN|NR 就是/AD /VV|VA
match: /PN|NN|NR 就是/AD 不/AD
match: ^ 就是/AD /VV|VA
match: /NN|NR|PN 就/AD 在/VV
match: /PN|NN 就/AD 不/AD /VV|VA
unless: 如果|假如|假使|要是|若|一旦|只要|既然|万一|就算|再不
unless: /CS
unless: /P 就/AD
unless: ^ 别/AD 说
unless: 就/AD 要/VV
unless: 就/AD /VV /AS
unless: 想/VV ... 就/AD
unless: 要/VV ... 就/AD
unless: 一/AD ... 就/AD
unless: 一/CD ... 就/AD
unless: 不/AD 是/VC ... 就/AD 是/VC
unless: 不/AD 是/VC ... 就是/AD
unless: /VV|VA 就/AD 不/AD
unless: /VV|VA 就/AD /VV|VA
unless: 就/AD 不/AD ... 了/SP
unless: 就/AD 是/VC ... 了/AS
unless: 就/AD /PN|NN|NR /CD|M
unless: 就/AD /PN 聪明/VA
unless: 就/AD /PN 没/AD /VV|VA
unless: 就在/AD
unless: 就/AD 在/VV 这/DT
unless: ^ 就/AD
```

**Notes:** The rule matches 就 directly before 是, a verb, or a negated verb, with a subject (noun or pronoun) before it. It also accepts the fused 就是/AD token, and 就在 for emphatic location. Precision comes first, so borderline cases are excluded. The exclusions are:
- conditional and temporal frames (如果, 一……就, 想/要……就);
- preposition 就/P;
- 就要 "about to";
- 不是……就是……;
- the X就X indifference pattern;
- sentence-final 了 after 就不 (a temporal result rather than emphasis);
- 就是……了 (concession);
- 就 + quantified subject (small quantity).

## 2-75
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
<!-- revised: the old patterns used `_*` and `‖`, which the rule syntax does not support, so they never matched -->
**Point:** Asking for opinion or confirmation with "怎么样" and with tag questions "好吗 / 可以吗 / 行吗 / 对吗 / 是吗"

**Covers:** Open-ended questions that end in 怎么样 ("How is…?" / "What do you think of…?"). Tag questions in which a short final clause is just a confirmation word (好 / 对 / 是 / 可以 / 行) plus 吗, added after a statement or suggestion.

**Does not cover:** 不怎么样 ("not so good", HSK 6-30). Plain 吗 questions such as 你好吗 or 好吃吗 (HSK 1-45). Other question words (HSK 1-46, 2-76). 怎么样 that is not at the end of its clause.

```rule
match: 怎么样/VV|VA $
match: ^ 好|对|是|可以|行/VA|VC|VV 吗/SP $
unless: 不|很/AD 怎么样
```

**Notes:** The patterns now use the supported syntax: `...`, `^` and `$`. Clause boundaries come from the punctuation, so the tag question has to be its own clause, made up only of the confirmation word and 吗. This keeps out 你好吗 and 好吃吗, where the word before 吗 sits inside a longer clause. 怎么样 is accepted when tagged VV or VA, because the tagger varies. 不怎么样 is excluded as HSK 6-30. Known risk: a bare 是吗？ used alone as a reply could still match.

## 2-76
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Asking questions with 什么时候, 什么样 and 怎么样 to ask "when?", "what kind of?" and "how is...?" / "what is...like?"

**Covers:** 怎么样 at the end of a clause, asking how something is (X + 怎么样 / 你觉得 X 怎么样). 什么时候 asking "when?". 什么样 asking "what kind of?" (什么样的 + noun).

**Does not cover:** These words in statements, such as "我不知道你什么时候来". 为什么 questions (HSK 1-46). 不怎么样 (HSK 6-30). 不管怎样 (HSK 5-65). A bare "怎么样？" tacked on to a suggestion, which is 2-75. Questions that use only 吗 (HSK 1-45).

```rule
match: 怎么样 $
match: 什么 时候
match: 什么 样
unless: 不 怎么样
unless: ^ 怎么样 $
unless: 不管|无论
unless: 知道
```

**Notes:** The tokenizer keeps 怎么样 as one word, usually tagged VV or VA. It splits 什么时候 into 什么 + 时候 and 什么样 into 什么 + 样, so the rule matches those pairs as separate tokens. 怎么样 only counts at the end of a clause, which covers "X 怎么样？" and "你觉得 X 怎么样？". The unless lines remove "不怎么样", a clause that is just "怎么样" (the 2-75 tag question), 不管/无论 concessives, and embedded questions after 知道. 怎样 is left out on purpose, because at the end of a clause it often appears in statements such as "想怎样就怎样". This favours precision over recall.

## 2-77
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Questions with "ne" (呢) — asking "what about...?" by stating a topic/noun/pronoun followed by 呢
**Covers:** Elliptical "bounce back" questions where a short verbless clause consisting only of a noun, pronoun or noun phrase (a demonstrative plus measure word, a time word, a place name, or a possessive) is followed by 呢, e.g. 那个呢？ / 你呢？ / 明天晚上呢？ / 上海呢？
**Does not cover:** Modal particle "ne" in statements (还早呢, 我吃饭呢); softening "ne" in 还是 or interrogative-word questions; 万一…呢; progressive 在/正/着…呢; A-not-A questions with 呢; any clause that contains a verb or adverb before 呢.

```rule
match: ^ /PN|NN|NR|NT|DT|M|JJ|DEG|DEC|OD _? _? 呢/SP $
unless: /AD
unless: /VV|VA|VE|VC|P|AS|CC|CS|BA|LB|SB|DER|DEV
unless: 万一
```

**Notes:** The rule only fires when the clause containing 呢 is made up purely of nominal material (pronoun, noun, proper noun, time noun, demonstrative, measure word, modifier, 的) and then ends with 呢. This captures the elliptical "what about X?" question. Any verb, adjective predicate, adverb, preposition, aspect marker or conjunction in that clause blocks the match, which excludes modal-particle statements, progressive 呢, A-not-A and 还是 questions, and 谁的…呢 questions. 万一 is excluded explicitly. The optional wildcard slots `_?` allow up to three words, but the unless lines ensure none of them is a verb or other predicate material. Because the clause must start at the beginning, the question can follow a previous clause or sentence.

## 2-78
<!-- written by claude-haiku-4-5 on 2026-11-09; edit freely -->
**Point:** Affirmative-negative questions using verb/adjective repetition with 不
**Covers:** Questions formed by stating a verb or adjective, then 不, then repeating the verb/adjective (or its first character for two-character words); also 有没有 pattern for yes/no questions
**Does not cover:** Questions using 吗, 呢, 吧, 还是, or question words (什么、谁、怎么、etc.); tag questions with 是不是 at the end are only counted if the main clause itself shows the V/不V or A/不A pattern

```rule
match: /VV 不/AD /VV
match: /VA 不/AD /VA
match: /VA 不/AD /AD /VV
match: /VV 不/AD /VV /AD /VV
match: /VV 不/AD /VA
match: /VE 没有/VE
unless: SP
```

**Notes:** The rule prioritizes matching the core V/不V and A/不A patterns. Two-character verbs and adjectives are handled by allowing the first character to be followed by 不 and then either another single character or the full repeated form (captured by patterns like `/VV 不/AD /VV`). The 有没有 pattern is included as an alternative yes/no question form listed in the grammar point. The `unless: SP` line filters out sentences where these patterns appear with sentence-final particles like 吗, 呢, or 吧, since those represent different grammar points. Clauses are checked independently (separated by punctuation), so tag questions at the end of statements are excluded unless the statement itself demonstrates the affirmative-negative structure.

## 2-81
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Expressing "about to" with 要/快要/就要⋯⋯了

**Covers:** Sentences with 就要 or 快要 followed by a verb phrase and 了, and clause-initial 要 + verb + 了 (e.g. 要下雨了). Also covers 还有 + number + duration + 就 (要) + verb + 了, which marks an event that is a set time away.

**Does not cover:** 要 meaning "want" or "need"; 要不是 "if it weren't for"; 要是 "if"; 不要 negative commands; 要 + verb as future intention with a specific future time; other uses of 要 with different meanings.

```rule
match: 就/AD 要/VV ... 了/SP
match: 就/AD 要/VV ... 了/AS
match: 快要/AD ... 了/SP
match: 快要/AD ... 了/AS
match: 快/AD 要/VV ... 了/SP
match: 快/AD 要/VV ... 了/AS
match: ^ 要/VV ... 了/SP
match: 还/AD 有 /CD ... 就/AD ... 了/SP
match: 还/AD 有 /CD ... 就/AD ... 了/AS
unless: 不/AD 要/VV
unless: 要/VV 不/AD
unless: 想/VV 要/VV
unless: 非/AD 要/VV
unless: 总/AD 要/VV
unless: 要/VV 看/VV
unless: 要是
unless: 要不是
```

**Notes:** The earlier rule used `.*`, which is not valid syntax, so nothing matched. The new rule uses only `...` for gaps and allows the final 了 to be tagged either SP or AS. Bare 要 is accepted only at the start of a clause and must end in sentence-final 了, which keeps out 要 meaning "want". The 还有 patterns require a number right after 有 and a 就 before the final 了, so 还有别的问题吗 does not match. The unless lines exclude negative commands, 要不, 想要, 非要, 总要, 要看, and 要是/要不是 conditionals.

## x-erhua
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Erhua words (哪儿, 玩儿) and question words that stay in the position of the thing being asked about (谁, 什么, 哪个, 哪里/哪儿, 什么时候, 为什么, 怎么, 多少).

**Covers:** Real questions with a question word in place, such as 你的家在哪儿？, 你是谁？ and 他去哪儿了？. Also sentences with 玩儿 (我们出去玩儿吧), and the bare question words given as labeled examples.

**Does not cover:**
- "Some" uses with a question word, e.g. 我在哪儿见过他 (somewhere). These are excluded only through the 在 + 哪儿/哪里 … 过 pattern.
- "Every" uses, with 都 or 也 after the question word, e.g. 哪儿都不想去, 谁都喜欢.
- 不管/无论 + question word, which is a "no matter what" use.
- 一会儿, which belongs to other HSK points.

```rule
match: 哪儿|哪里/PN
match: 玩 儿
match: 玩儿
match: 谁/PN
match: 什么/PN
match: 什么/DT
match: 哪/DT 个/M
match: 多少/CD
match: 怎么
match: 为什么
unless: 哪儿|哪里|哪|谁|什么|怎么|多少 ... 都|也/AD
unless: 在/P 哪儿|哪里 ... 过/AS
unless: 不管|无论
```

**Notes:**
- The "some" and "every" filters are deliberately blunt, so some good questions will be lost. One example is 你在哪儿见过他？, which is a real question but is excluded by the 在 … 过 filter.
- Embedded questions such as 我不知道他是谁 still match. Add an `unless` on 知道 if they should be dropped.
- 什么/DT and 怎么 are matched under any tag because the tagger varies. 为什么 is tagged VV.
- 好玩儿 tokenized as one word is not matched, because only 玩儿 and 玩 + 儿 are.
- 一会儿 is deliberately not matched.

## x-yibian
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Expressing two actions happening simultaneously using 一边...一边... structure

**Covers:** Sentences where 一边 directly precedes a verb, as in 一边 V₁ (，) 一边 V₂. This includes the comma-separated form, where each 一边 sits in its own clause, and the single-clause form (我们一边走一边聊).

**Does not cover:** 一边 as a noun or location word ("one side", 这一边); 一边是…一边是… contrasts (一边 followed by 是, not a verb); passive or unintentional simultaneous events (use 的时候 instead).

```rule
match: 一边/AD /VV
```

**Notes:** Patterns only match within one clause, so the earlier rule `一边/AD _ 一边/AD` could never fire on the usual comma-separated form (一边吃饭，一边玩). This rule instead looks for a 一边 tagged AD immediately followed by a verb (VV). That pattern appears in each half of the structure, so it matches both the comma-separated and the single-clause forms. Requiring a following verb excludes the noun/locative 一边 and 一边是… contrasts. The shortened form 边 is not covered and would need a separate rule.

## 3-01
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Ordinal numbers with 第 (dì)
**Covers:** 第 + number (+ measure word) (+ noun) used to express ordinal position ("first", "second", "third"). This includes 第一次, 第二个月, 第一周, and bare predicate use such as 我们班第一.
**Does not cover:** 老 or 小 in other uses (频率副词 "always", age/size comparison, or inside words like 老师, 小说). It also does not cover floor numbers without 第, such as 七楼.

```rule
match: 第一|第二|第三|第四|第五|第六|第七|第八|第九|第十|第十一|第十二|第十三|第十四|第十五|第二十|第一百/CD
```

**Notes:** The tagger fuses 第 with the number into a single CD token (第一/CD, 第二/CD), so the earlier patterns on a standalone 第/CD never matched. The rule now matches the fused tokens directly. A fused 第N token is itself the ordinal structure, so the optional measure word, noun, or bare predicate use (班第一) does not need to be checked. The old unless clauses on 老 and 小 are removed. They could not help precision, because those words do not appear inside a 第N token, and they only risked blocking valid sentences that also contain 老 or 小. 老是 and 比…大/小 sentences contain no 第N token, so they are not matched.

## 3-16
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Using 再 (zài) to express "again" for future actions, "and then" for sequencing events (including 先…再说), and the contrasting 又 for an expected repetition ("again" in the near future, as in 又要/又可以/又能/又是).

**Covers:** Future repetition ("again", including 再说一遍), sequencing with 先…再… and 先…再说, "another" with quantity phrases, and 又 + 是/要/可以/能 for something expected to happen again.

**Does not cover:** 一再/再三 (repeatedly), 再不…就… (last chance warning), 再也不/没 (never again), 再也…也… (even so), 再说 as "in addition" (clause-initial or standalone), and 又…又… (both…and).

```rule
match: 再/AD ... /VV
match: 再说/VV /CD
match: 先/AD ... 再/AD
match: 先/AD ... 再说/VV $
match: 又/AD 要|可以|能/VV
match: 又/AD 是/VC
unless: 再/AD 也/AD
unless: 再/AD 不/AD
unless: 一再/AD
unless: 再三/AD
unless: 再/AD 说/VV $
unless: 又/AD ... 又/AD
```

**Notes:** The tagger often treats 再说 as a single verb token, so 你可以再说一遍吗 and 先…再说 need their own patterns. 再说 + number covers "say it again". 再说 at the end of a clause counts only after 先 (先吃饭再说). A clause-initial 再说 ("in addition") is not matched, because it is followed by a comma and not by a number. The 又 patterns cover the labeled examples where 又 marks an expected repetition (又是星期一, 又要开会, 又可以…, 就又能…). They are limited to 是/要/可以/能 to stay precise, and are blocked when a second 又 appears (又…又…). The earlier exclusions for 再也, 再不, 一再 and 再三 are unchanged.

## 3-21
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** 自从 ("ever since") marks a starting point in the past. It is often paired with 以来 or 以后/后 at the end of the time phrase, and the shorter 自 or 从 can replace 自从 in the 以来 pattern.

**Covers:**
- 自从 + event or time, followed by a main clause, e.g. 自从来到中国，他就喜欢上了中国菜。
- 自从 + event + 以后/后/以来.
- 自/从 + event or time + 以来, e.g. 自改革开放以来… and 从毕业以来….

**Does not cover:**
- 从 on its own, which marks a place or future starting point ("from").
- 从…以后/后 without 自, which is ambiguous with plain 从 + time ("from now on").
- 以来 or 以后 with no 自/从/自从 before it.

```rule
match: 自从
match: 自|从/P ... 以来
```

**Notes:**
- The rule has no `unless:` line because 自从 is unambiguous in the course data.
- 自从 is matched as a whole word with any tag, because the tagger could mis-tag it.
- 自从 may appear after a subject, e.g. 我自从来了以后, so the pattern is not anchored to the clause start.
- The 自|从 plus 以来 pattern requires the preposition tag P and both words in the same clause. This keeps out 从 used for place, such as 从北京来.
- 从…后 and 从…以后 are deliberately left out for precision. Add them only with extra checks.

## 3-23
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
<!-- revised by claude on 2026-11-09; edit freely -->
**Point:** Using 为 as a preposition to express "for" (beneficiary or purpose)
**Covers:** (1) 为 + beneficiary/goal + verb/adjective showing an action or emotion done for them (including 是…为…的 and 不为…); (2) the 为…而… structure expressing purpose with effort or action
**Does not cover:** 为了 (3-26, 6-37); 因为 (because); 以为 (mistakenly think); 为 as main verb or copula (是/变为/成为); fixed expressions such as 以…为, 作为, 为期, 为此, 为什么, 为难, 为的是, 为…所…

```rule
match: 为/P _ ... /VV|VA
unless: 以 ... 为
unless: 为/VC
unless: 为 什么
unless: 为 的 是
unless: 为 ... 所
unless: 作为
unless: 因为
unless: 以为
```

**Notes:** The earlier patterns required a stray extra word before 为 and a bare 为-plus-noun adjacency, so they missed nearly every example. The revised rule matches 为 only when the tagger marks it as a preposition (P), and requires a following word and then a verb or adjective later in the same clause. This covers 为我们高兴, 为孩子而活, 是为你准备的 and 为公司的发展贡献. 为了 is a separate token and so is never matched. The unless lines block 以…为, 为什么, 为的是, 为…所 passives, 作为, 因为 and 以为.

## 3-26
<!-- written by claude-haiku-4-5 on 2026-11-09; edit freely -->
**Point:** Using 为了 or 为...而 to express the purpose or beneficiary of an action
**Covers:** Sentences where 为了 or 为...而 introduces the purpose/beneficiary of an action, appearing before the main verb (or after 是 in copular sentences)
**Does not cover:** Cases where 为 is part of other verbs like 成为 or 变为; sentences using only 因为 (which expresses reason/cause, not purpose)

```rule
match: 为了/P
match: 为/P ... 而/MSP
unless: 成为|变为
```

**Notes:** The rule matches two structures: (1) 为了 as a preposition introducing purpose, which can appear clause-initially or after 是; (2) the pattern 为...而 where 为 is tagged as P and 而 as MSP, with any number of words between them. The unless clause excludes verbs 成为 and 变为 where 为 is part of the verb root, not a preposition. The tagger sometimes marks 为了 as VV when it's part of a verb compound (e.g., 变为了), which the unless clause filters out. Sentences with 因为 alone are excluded because they express cause/reason rather than purpose.

## 3-39
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** 从……起 marks the starting point of a time or event ("from now on"). This rule also accepts the closely related uses of 从 (starting point, source) and 离 (distance in time or space) that the course labels as this point.

**Covers:**
- 从 + time or event + 起 or 开始, e.g. 从今天起, 从现在起, 从我上小学开始.
- 从那以后 and 从今以后 ("ever since then", "from now on").
- 从 + person or place + 里 or 上 as a source, e.g. 从我包里拿, 从口袋里掏出.
- 从这件事 + inference, e.g. 从这件事可以看出….
- 离 as a preposition for distance or time gap, e.g. 离毕业还有几个月, 离结婚还远呢.

**Does not cover:**
- 看起来, 起来 as a direction complement, 比起, and 一起, because none of them has 从 or 离 as a preposition.
- 从 used as an adverb, as in 从不, 从没, 从小.
- 从事.
- 从……来看 or 从这个角度看, which is the separate HSK 5 point.
- Bare motion-from-place sentences such as 从入口进去 or 太阳从东方出来. These are left out for precision.

```rule
match: 从/P ... 起/LC
match: 从/P ... 以后|以前/LC
match: 从/P ... 开始
match: 从今以后/NT
match: 从/P ... 里|上/LC
match: 从/P /PN 身上
match: 从/P 这/DT M 事|事情/NN
match: 离/P
unless: 从/P ... 来/MSP 看
unless: 从/P ... 看 $
unless: 从/P ... 来看|来说|看来
```

**Notes:** 离/P is accepted on its own because it is almost always the distance or time-gap use. The 从/P patterns need a following cue (起, 以后, 开始, 里, 身上, or 这件事), so plain motion sentences are skipped; add cues carefully, since each one widens the rule. The `unless` lines remove 从……看 / 来看 sentences that belong to HSK 5-26. A sentence like 从那以后，我看… is only excluded if 看 ends the same clause as 从. 从今以后 is tagged as a single NT token, so it has its own pattern.

## 3-40
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** 对...来说 (and the fixed perspective phrases 相对/严格/具体来说) to express "for/to someone" or to set a viewpoint

**Covers:** Sentences with 对 + [person/group/topic] + 来说 introducing a viewpoint before the comment. Also clause-initial 相对来说, 严格来说 and 具体来说.

**Does not cover:** Sentences with 而言 instead of 来说. Sentences with 就...来说 (scope limiting, where 就 is the preposition). Sentences with 拿...来说 (HSK 5-29). Other uses of 对 as "towards/against" without 来说. Bare 一般来说, which is a separate point (4-28).

```rule
match: 对/P ... 来/MSP 说/VV
match: ^ 相对|严格|具体 来/MSP 说/VV
unless: 而言/LC
unless: 而言
unless: 就/P
unless: 拿
```

**Notes:** The tagger splits 来说 into 来/MSP 说/VV. The main match now requires the preposition 对/P earlier in the same clause, so bare 来说 uses are no longer matched. The unless lines remove the 而言 variant, 就...来说 (the counter-examples 就这些事情来说 and 就我们家来说), and 拿...来说. 对于 is a different token and is not matched, because it belongs to 4-14. 总的来说 and 一般来说 are left out because their tokenization is uncertain and 一般来说 is a separate point.

## 3-46
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Result complements 到, 住, 开 and 出(来) after a verb, showing that the action reached its result (including 看见/听见 and 打开)

**Covers:** Verb + 到/住/开/出/出来 where the complement marks the result of the action, e.g. 看到, 买到, 接住, 抓住, 张开, 想开, 想出, 做出来, 听出来. Also the fused sense-verb compounds 看见/听见/遇见 and 打开.

**Does not cover:** 到 meaning "arrive at a place", "until (time)", "less than", or "from...to". Also excluded: 住 as a main verb "to live" (想住, 住在), 开 as "to drive/hold" (想开车), 出 in 出门/出国, and modal + 开/出/住 where the second verb is the main verb.

```rule
match: /VV 到|住|开|出|出来/VV
match: 看见|听见|遇见|碰见|梦见|打开 /VV
unless: 一直/AD ... 到/VV
unless: 从/P ... 到
unless: 来|去|回|走|跑|飞|坐|搬|送|带|赶|等|玩|睡|忙|工作|聊|坚持|待|站 到/VV
unless: 到 /NR
unless: 到 /NT
unless: 到 /CD
unless: 到 /VV 家|机场|现场|了解|达|时候|底
unless: 想|要|会|能|可以|愿意|应该|敢|准备|打算|喜欢|希望|决定|得 住/VV
unless: 要|会|能|可以|愿意|应该|敢|准备|打算|喜欢|希望|决定|得 开|出|出来/VV
unless: 住/VV 在|着|进
unless: 开/VV 车|门|会|店|灯|窗|玩笑|始|学|工|机|火|花|药|刀
unless: 出/VV 门|国|差|发|席|版|名|现|院|事
```

**Notes:** The complements are tagged VV, so the match looks for a verb followed directly by 到/住/开/出/出来. Fused compounds (看见, 听见, 打开) are separate tokens and have their own match line. The unless lines remove 到 uses that mean arrival, "until" or "from…to" (a place or time word after 到, motion or duration verbs before it). They also remove cases where 开/出/住 are really main verbs after a modal (想开车,

## 3-48
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Potential complements showing whether an action is possible: Verb + 得/不 + result, direction or ability complement (懂, 见, 完, 到, 着, 了, 下, 起, 动 ...).

**Covers:**
- Verb + 不 + complement, meaning "cannot" (看不懂, 去不了, 找不到, 吃不下, 听不见, 买不起, 走不动).
- Verb + 得 + complement, meaning "can" (听得懂, 看得见, 吃得完, 放得下, 帮得了).
- 得了 here is tagged as the particle AS, as in 帮得了 or 走得了.

**Does not cover:**
- 不得不 "have to".
- 差不多 "approximately".
- 少不了 "unavoidable".
- Degree or state complements with 得 (说得清楚, 做得好, 画得怎么样, 堵得要命, 得很, 不得了).
- 害得 "causing an undesirable result".
- Repeated verb questions of the form A不A (去不去, 来不来).

```rule
match: /VV 不/AD 了|到|着|见|懂|完|下|起|动|上|来|去|出|进|过|开|住|通|会
match: /VV 得/DER 了|到|着|见|懂|完|下|起|动|上
match: /VV 得了/AS
unless: 不得不|差不多|少不了|害得|不得了|得不得了|得很|得死了|得要命|去不去|来不来|上不上|下不下|对不对|急坏
```

**Notes:**
- The 不 pattern needs a verb, then 不 tagged AD, then one of a closed list of complement words. This keeps out 不 + adjective or verb forms such as 想不想 and 好不好.
- The 得/DER patterns accept only a closed list of result and direction complements. Adjective complements (好, 清楚, 干净) are left out because they are mostly degree or state complements, so a few true potential uses will be missed to keep precision high.
- The "unless" list removes sentences with 害得, which are labelled as a different point even when they contain 睡不着 or 打不通.
- It also removes 不得不, 差不多, 少不了, 得很 and the other degree-complement look-alikes.
- Each pattern matches within a single clause.

## 3-60
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** The adverb 也 (yě) meaning "also" or "too", placed right after the subject and before the verb/adjective to express that someone/something shares the same quality or action as another.

**Covers:**
- Subject + 也 + Verb (他也喜欢。)
- Subject + 也 + (很) + Adjective (他也很胖。)
- Subject + 也 + 是 / 有 + Noun (我也是学生。)
- Subject + 也 + Negation + Verb (我也不知道。)
- Subject + 也 + Modal Verb + Verb (他们也会去吗？)
- Subject + 也 + 没有 (他也没有。)
- Simple parallel structures showing an "also" relationship between two similar clauses

**Does not cover:**
- Conditional/concessive structures (即使...也, 就算...也, 哪怕...也, etc.) where 也 means "still/even so"
- "Never again" with 再也不/没
- 甚至连...也 (even/let alone structure)
- 宁可...也 (rather...than structure)
- 一点儿也不 and 一 + Measure Word + Noun + 也 + 不/没 (not at all / not even one)
- 什么/谁 + Noun + 也 (everything / nothing)
- 连...也 (even...as well)
- 倒也 (contrariness)
- Structures with preceding markers like 别说, 就是, 不但, 无论, 不管, etc.

```rule
match: /PN|NN|NR|NT 也/AD ... /VV|VA|VC|VE
match: /PN|NN|NR|NT 也/AD 没有/AD
unless: 再/AD 也/AD
unless: 甚至
unless: 宁可
unless: 连
unless: 一/CD ... 也/AD
unless: 一点儿
unless: 什么 ... 也/AD
unless: 谁 ... 也/AD
unless: 就算
unless: 就是
unless: 即使
unless: 哪怕
unless: 别说
unless: 倒
unless: 不但
unless: 无论
unless: 不管
```

**Notes:** The subject (pronoun, noun, proper noun or time noun) must sit directly before 也/AD, and a predicate (verb, adjective, 是, 有, or 没有) must follow. The earlier version had a stray `_` between the subject and 也, which demanded a second 也 and made it miss every true example. Requiring the subject to be adjacent to 也 also keeps out 一点儿也不, 一…也没, and 什么…也, which have other words in that slot. Explicit `unless:` lines remove the concessive, emphatic, and "never again" structures, including 别说, 就是, 再也, 连, and 甚至.

## 3-67
<!-- written by claude-haiku-4-5 on 2026-11-09; edit freely -->
**Point:** 不是⋯⋯，就是⋯⋯ — expressing that a situation is one of only two possibilities
**Covers:** The structure with 不是 + A，就是 + B where A and B are the two exhaustive alternatives (can be verbs, adjectives, nouns, or phrases), appearing in the same clause or across punctuation-separated clauses
**Does not cover:** 要不是 (if it were not for), 就是 + hypothetical + 也 (even if), 而是 (but rather), 就是 when used for identification/emphasis alone, 别说⋯就是 (let alone), concessive 是⋯就是 (X is X, but...), 岂不是 (rhetorical), or 要是/就 (if-then conditional)

```rule
match: 不/AD 是/VC ... 就是/AD|CC
match: 不/AD 是/VC ... 就/AD 是/VC
unless: 而/AD|CC 是/VC
unless: 要/VV 不/AD 是/VC
unless: 也/AD 不/AD 是/VC
unless: 别说/AD
unless: 岂不/AD 是/VC
unless: 是/VC ... 是/VC ‖ 就是/AD
unless: /VA 是/VC /VA ‖ 就是/AD
```

**Notes:** The pattern looks for 不是 followed by content and then 就是 (tagged as AD or CC depending on position). The unless clauses exclude: (1) 而是 structures (different grammar), (2) 要不是 (counterfactual), (3) X也不是⋯ (different pattern from HSK 5-60), (4) 别说 (let alone structure), (5) 岂不是 (rhetorical), (6) concessive structures where an adjective + 是 + adjective precedes 就是. The core pattern requires both parts present in the sentence to confirm the two-alternatives meaning.

## 3-69
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Expressing "if… then…" with 要是/要/要不是 and 就

**Covers:** Conditional sentences where 要是 (or 要 / 要不 after a subject) introduces the condition clause, with the result in the following clause (usually with 就). Also 要不是 ("if it weren't for") constructions and the fixed expression 要我说 ("if you ask me").

**Does not cover:** 就 as a simple adverb with no conditional; 就是 used for emphasis or identification; 要 as a standalone modal verb; other conditionals such as 只要, 假如, 万一, 一旦.

```rule
match: 要是/CS|AD
match: 要不是/CS|AD
match: ^ 要/CS|VV 我/PN 说/VV $
match: ^ PN 要/VV 再说/VV $
match: ^ PN 要不/AD VV $
```

**Notes:**
- A pattern can only match inside one clause, so the rule cannot require 就 in the second clause. The condition marker itself is the trigger: 要是, 要不是, or the narrow fixed forms above.
- 要是 and 要不是 are accepted as CS or AD, because the tagger varies between them.
- 要我说 is matched only when it is a whole clause, so 要我说话 and similar do not match.
- Bare 要 is not matched, because it is usually a modal verb ("want to"). Only 你要再说 and 他要不听 are matched, and only as a complete clause.
- Sentences with 就是 are not excluded. Labeled positives such as 要我说，他就是太懒 contain it, and the condition marker is what decides the match.

## 3-77
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Emphasizing with 很是 (hěnshi), a degree adverb that adds emphasis to statements. The official HSK examples use the related order 是很 + Adj./Verb (这位经理是很负责, 那电影是很有意思).

**Covers:** Subj. + 很是 + Adj. or psychological verb (including 很是不 + Adj., and 很是 + 会/能 + Verb). Also Subj. + 是很 + Adj./Verb at the end of a clause, as in the official examples.

**Does not cover:** Other uses of 是: basic equative 是, 是…的, 凡是, 于是, 要不是, 岂不是, 有的是, 老是, 总是, 还是, 就是…也, 不是…就是, 是否, and negated or questioning 不是很… / 是不是很….

```rule
match: 很/AD 是/VC 不? /VA|VV
match: 很/AD 是/VC 精神/NN
match: /NN|PN|NR 是/VC 很/AD /VA|VV $
unless: 凡是
unless: 于是
unless: 要不是
unless: 岂不是
unless: 有的是
unless: 老是
unless: 总是
unless: 还是 ... 吧
unless: 就是 ... 也
unless: 不是 ... 就是
unless: 不 是
unless: 是否
unless: 是 很 ... 的
```

**Notes:** The first two lines cover the 很是 + Adj./Verb pattern. This includes 很是喜欢 followed by an object containing 的, which the old `是 ... 的` exclusion wrongly blocked. The tagger labels 精神 as NN, so that word has its own line. The third line covers the official 是很 + Adj./Verb examples. It requires a noun or pronoun subject before 是 and the predicate to end the clause, which keeps out 就是很, 还是很, 总是很, and 是很…的 noun-modifier uses. Negated 不是很… and 是不是很… are excluded. The 是…的 exclusion is kept only in the narrow form `是 很 ... 的`.

## 3-79
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Expressing "already" with 都⋯⋯了, used emphatically to indicate that something has already happened or reached a point, often implying the speaker's attitude (impatience, surprise, a push to act).

**Covers:** Clause-initial 都 followed directly by a time or duration expression (number + measure word, optionally with a noun, or a time noun) and then sentence-final 了. Examples: 都十一点了, 都三天了, 都一个小时了, 都二十一世纪了.

**Does not cover:** Other uses of 都: universal "all/every" with a subject before 都 (全校都放假了, 他们都在上海), 也都, 除了⋯⋯都, question-word 都, 不管/无论⋯⋯都, 连⋯⋯都, 何况, 从来都, 什么都, 全都. It also does not cover the subject + 都 + predicate + 了 form (饭都凉了), because it cannot be told apart from the "all" reading without more context.

```rule
match: ^ 都/AD CD M 了/SP
match: ^ 都/AD CD M NN 了/SP
match: ^ 都/AD CD NT 了/SP
match: ^ 都/AD NT 了/SP
unless: 不管|无论|何况|连|除了
```

**Notes:** The rule only matches 都 at the start of a clause, directly followed by a time or duration phrase and then 了. This is the clearest form of "already" 都⋯⋯了. It no longer requires 了 to end the clause, and it no longer excludes 怎么 or other question words, because the labeled example 都三天了，他怎么还没回来 uses 怎么 in a following clause. Subject + 都 + verb + 了 sentences like 全校都放假了 are not matched, since they are usually the "all" use of 都. The cost is that sentences like 饭都凉了 are missed, in return for precision.

## 3-80
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Expressing indifference or concession with "X就X(点儿)(吧)": a word or phrase is repeated around 就, and the speaker accepts the situation with an "I don't care" or "fine then" attitude.

**Covers:**
- Verb + 就 + Verb, with or without 吧 (走就走, 走就走吧, 赌就赌)
- Adjective + 就 + Adjective, with or without 吧 (慢就慢吧)
- Verb/Adj. + 了 + 就 + Verb/Adj. + 了 (坏了就坏了)
- Negation + Verb/Adj. + 就 + Negation + Verb/Adj., with an optional subject before it (不帮就不帮, 她不高兴就不高兴, 他没来就没来)
- Optional 点儿 between the repeated element and 吧 (忙就忙点儿吧)

**Does not cover:**
- Simple suggestions with 吧 and no X就X repetition (我们走吧)
- 就 in conditional or "then" structures (只要...就, 如果...就, 既然...那就买吧)
- 就 with other meanings (emphasis, earliness, scope, 就要, 就是)
- 吧 for asking opinions or softening statements
- Clauses that contain both 就 and 吧 but do not form the X就X pattern (你们就分手吧)

```rule
match: ^ /VV|VA 就/AD /VV|VA 点/CD? 儿/CD? 吧/SP $
match: ^ /VV|VA 了? 就/AD /VV|VA 了? $
match: ^ /PN|NN|NR? 不|没/AD /VV|VA 了? 就/AD 不|没/AD /VV|VA 了? 吧/SP? $
unless: 就/AD 懂|会|知道|行|好|对|能|有|成|算|够|清楚|明白|完|到|着|是
```

**Notes:** The rule cannot check that the two copies are identical, so it relies on structure instead. The clause must be just two verbs or adjectives around 就/AD, with an optional 了, 点儿 or 吧. Negated versions may also have a subject. This excludes "那就买吧" and "你们就分手吧", because their first word is not a VV or VA. The final `unless` blocks resultative or conditional readings such as 看就懂 and 吃了就好, where the second word differs from the first.

## 3-81
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Rhetorical challenge with 什么 after a verb or adjective (X什么（啊）)

**Covers:** Verb/adjective + 什么 + repeated verb/adjective (你买什么买, 胖什么胖啊); separable verb split by 什么 at the start of a clause (离什么婚, 道什么歉啊, 开什么玩笑); clause-initial psychological verb or adjective + 什么 (+啊/呀) (担心什么啊, 难过什么啊, 高什么啊); 玩儿什么.

**Does not cover:** 什么 as a genuine question word (你吃什么？); 什么都/也 meaning "everything"; 什么的 ending a list; 什么 in compounds like 什么时候.

```rule
match: /VV 什么/PN /VV
match: /VA 什么/PN /VA
match: ^ /VV 什么/PN /NN 啊|呀|吧? $
match: ^ 开 什么 玩笑 啊|呀|吧?
match: ^ 担心|难过|紧张|着急|害怕|生气|舒服|高兴|伤心|激动|累|急|怕|哭|笑|胖|大|高|聪明|忙|烦|愁|慌 什么/PN 啊|呀|吧? $
match: ^ 玩 儿 什么/PN 啊|呀|吧? $
unless: 什么 都
unless: 什么 也
unless: 什么 的
unless: 什么/PN 都/AD
unless: 什么/PN 也/AD
unless: 什么/DT 的/DEG
```

**Notes:**
- The old rule's whole-sentence `unless: 都/AD` and `unless: 也/AD` wrongly blocked sentences like 你买什么买，钱都花光了, so they are removed. The "everything" exclusion now only applies when 什么 is directly followed by 都/也.
- In the data 什么 is tagged PN even in 离什么婚 and 道什么歉, so the rule uses 什么/PN. The broad "/VV 什么/DT" pattern is dropped because it matched genuine questions like 你喜欢什么颜色.
- Non-repeated forms (X什么 with no repeat) are anchored to the start of a clause and end the clause. The bare-adjective/psych-verb form uses a word list, to avoid genuine questions like 吃什么？.

## 4-01
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** The auxiliary verb 得 (děi), "must / have to", placed before a verb phrase: Subj. + 得 + VP.
**Covers:** 得 tagged as a verb (VV) and followed by a verb, adverb, adjective, preposition or 把. It must come at the start of a clause or after a pronoun, noun, proper noun, time word or adverb. Examples: 我们得走了, 你得先付钱再进去, 在学校就得听老师的话, and a subject-less 得去看医生 at the start of a clause.
**Does not cover:** 得 tagged as the complement particle DER (degree, state and potential complements, 害得, 得很, 得不得了). It also excludes 得 meaning "to get" (他得了流感, 得了十分, 得出结果, 得知, 得到). The fixed words 不得不, 只得, 显得, 觉得, 记得, 值得 and 得分 are single tokens and do not match.

```rule
match: ^ 得/VV /VV|AD|VA|P|BA
match: /PN|NN|NR|NT|AD 得/VV /VV|AD|VA|P|BA
unless: 得/VV 出|知|到|以|胜|罪
```

**Notes:** The rule relies on the tagger giving děi the tag VV and the complement particle the tag DER. The "get" sense of 得 is also VV, so it is excluded by two checks. First, the word after 得 must be a verb, adverb, adjective, preposition or 把, which rules out 得了 (了 is tagged AS) and 得+noun. Second, the `unless:` line removes 得出, 得知 and similar splits. If a sentence such as "你得出去" is tokenized as 得 出 去, it will be missed, which is acceptable. A 得 with no overt subject in the middle of a clause (for example after a verb) is not matched, to keep precision high.

## 4-02
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** The word 人家 (rénjia / rénjiā): "other people", "I/me" (often coy, feminine speech), a previously mentioned or mutually known person, and "households" (户 + 人家)
**Covers:** Any occurrence of the whole word 人家, whatever tag the tagger gives it (PN or NN). That includes 人家 as a pronoun for others, for oneself, or for a known third person, 人家 in a possessive phrase such as 人家的事 (the tagger often marks it NN), and 人家 meaning households, as in 十户人家.
**Does not cover:** 别人 sentences that have no 人家. 别人 is a different word and appears in many sentences unrelated to this point, so the rule does not match it. Characters 人 and 家 that appear separately or inside other words (e.g. 人家庭) are also not covered, because the rule matches only the whole word 人家.

```rule
match: 人家
```

**Notes:** The earlier rule required the PN tag and excluded 户 + 人家/NN. The labeled examples show both are wrong. The tagger labels 人家 as NN in 那是人家的事, and the household use (多少户人家, 十户人家) is explicitly part of this grammar point. So the rule now matches the word 人家 under any tag and has no `unless` clause. Because it matches only the exact token 人家, it stays precise. The labeled 别人 example is deliberately left unmatched. Matching 别人 would let in many unrelated sentences, and a miss costs less than a wrong match.

## 4-08
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Expressing "again and again" or "repeatedly" with 一再 or 再三

**Covers:** Adverbial use of 一再 or 再三 directly before a verb (with optional 地) to indicate repeated or emphatic action

**Does not cover:** Other uses of these words that don't function as frequency adverbs modifying a verb phrase

```rule
match: 一再|再三/AD 地/DEV? /VV
```

**Notes:** The old rule had a stray `unless: ...` line. `...` matches any run of words, so that line matched every sentence and blocked all matches. I removed it. I also tightened the pattern so that 一再 or 再三 tagged AD must come immediately before a verb (VV), with only an optional 地 in between. This covers "Subj + 一再/再三 + Verb", the 地 variant, bare fragments like "一再强调", and the "经过再三考虑" form. It does not allow arbitrary words between the adverb and the verb, which keeps precision high.

## 4-22
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Sentence-final interjection 啊 (a) that adds a tone of urgency, exclamation, excitement, certainty, or concern. This also covers 啊 as a standalone interjection opening a sentence ("啊，我明白了。").

**Covers:** 
- 啊 tagged IJ at the start of a clause, as in "啊，你怎么在这里？".
- Exclamatory 啊/SP closing a clause after a degree adverb plus adjective ("这里好漂亮啊！", "你家真大啊！", "学中文真难啊！", "好香啊！").
- 啊/SP closing a short reply such as 对啊, 是啊, 行啊 or 可以啊.

**Does not cover:** 
- 啊 in enumeration/listing patterns (A啊，B啊，C啊), where 啊 follows a noun, name or verb phrase.
- 啊 in "X什么啊" patterns.
- 啊 in the middle of a clause.

```rule
match: ^ 啊/IJ
match: 真|太|好|多|这么|那么/AD /VA 啊/SP $
match: ^ 对|行|好|不错/VA 啊/SP $
match: ^ 是/VC 啊/SP $
match: ^ 可以/VV 啊/SP $
```

**Notes:** 
- Rules match within a single clause, so the earlier `unless` lines that tried to span clauses with ‖ could never work, and I removed them. The earlier pattern `^啊/IJ` was written without a space after `^`, so it most likely failed to parse. It is now `^ 啊/IJ`.
- The bare "啊/SP $" pattern is gone. It matched every listing example, because each list item ends its clause with 啊/SP and the rule cannot see neighbouring clauses.
- Instead, the sentence-final matches require an exclamatory context: degree adverb + adjective, or a short affirmative reply at the start of the clause. Listing 啊 follows nouns, names or verb phrases (花生啊, 小王啊, 看视频啊), so it is not matched.
- Some real sentence-final 啊 after other words will be missed. That is accepted in favour of precision.

## 4-24
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Expressing "one by one", "little by little" or "again and again" with the pattern 一 + Measure Word + 一 + Measure Word
**Covers:** Repeated "一 + measure word" meaning "one by one", "little by little", "gradually" or "again and again". It includes the optional 又 between the two parts (一遍又一遍). It works with time units (一天一天, 一年一年), classifiers and verb measure words (个, 点, 次, 遍, 步, 针), and with a noun repeated after the measure word (一个房间一个房间). It also covers the fixed forms 一针一线 and 一五一十.
**Does not cover:** Single instances of 一 + measure word (一下, 一些, 一点); "once a day" type frequency phrases with different measure words (一天一次); adverbial uses of 一向, 一直, 一再, 一定, 一律, 一起, 一旦; 一下子; 之一; 一样; and other 一-based expressions that are not repeated measure word patterns.

```rule
match: 一/CD 个/M 一/CD 个/M
match: 一/CD 点/M 一/CD 点/M
match: 一/CD 天/M 一/CD 天/M
match: 一/CD 年/M 一/CD 年/M
match: 一/CD 次/M 一/CD 次/M
match: 一/CD 遍/M 一/CD 遍/M
match: 一/CD 步/M 一/CD 步/M
match: 一/CD 句/M 一/CD 句/M
match: 一/CD 口/M 一/CD 口/M
match: 一/CD 月/M 一/CD 月/M
match: 一/CD 个/M _ 一/CD 个/M
match: 一/CD 个/M 又/AD 一/CD 个/M
match: 一/CD 天/M 又/AD 一/CD 天/M
match: 一/CD 年/M 又/AD 一/CD 年/M
match: 一/CD 次/M 又/AD 一/CD 次/M
match: 一/CD 遍/M 又/AD 一/CD 遍/M
match: 一/CD 针/M 一线/NN
match: 一五一十/CD
```

**Notes:** The previous rule used bare tag names (CD, M) as if they were words, so it never matched anything. The unless clauses also blocked ordinary sentences, because 别, 在 and 都 are tagged AD. This version matches the actual tokens: 一/CD, a specific measure word, then 一/CD and the same measure word. Listing each measure word explicitly keeps out mismatched frequency phrases such as 一天一次. The one-noun variant is limited to 个 (一个房间一个房间) to protect precision. 一针一线 is tokenized as 针 followed by the noun-tagged 一线, and 一五一十 is a single CD token, so both are listed as fixed forms.

## 4-25
<!-- written by claude-haiku-4-5 on 2026-11-09; edit freely -->
**Point:** Using 看来 to express the speaker's assessment or perspective on a situation
**Covers:** Sentences where 看来 appears at or near the start of a clause to introduce an assessment ("it seems," "apparently," "in X's view"), with or without the 在...看来 structure
**Does not cover:** Other uses of 看 and 来 as separate verbs; the HSK 5-31 point 在...看来 when it functions as a distinct framing structure without 看来 as a single unit

```rule
match: 看来/VV
match: 在/P _ 看来/VV
unless: 看起来/VV
```

**Notes:** The first pattern catches 看来 tagged as VV at the start of a clause or after a time word/adverb. The second pattern catches the 在 + NOUN/PN + 看来 structure. Both patterns treat 看来 as a unified adverbial marker of assessment. The rule excludes 看起来 (which means "looks like" visually, HSK 4-26) as a common false positive. One labeled example tags 看来 as AD (明天不会再下雨了), likely an error, but VV is the standard tag for this point; the VV match will still catch most correct instances. The 在...看来 pattern uses a wildcard for person/noun between 在 and 看来 to accommodate various subjects (我, 他, 朋友, 老板, etc.).

## 4-30
<!-- written by claude-haiku-4-5 on 2026-11-09; edit freely -->
**Point:** Expressing "ever since" with 以来 or 来 after a time period or event
**Covers:** Sentences with 以来 or 来 (tagged LC) that express a continuous state/action from a past time/event to the present, typically at the start of a clause
**Does not cover:** The verb 来自 (to come from) which is tagged VV, not LC; uses of 自 as a preposition or in other contexts; other HSK 3 grammar points like 自从

```rule
match: ... 以来/LC
match: ... 来/LC
unless: 来自/VV
```

**Notes:** The key distinguishing feature is the LC (localizer) tag for 以来 or 来. The verb 来自 (meaning "to come from") will be tagged VV, so the unless clause filters it out. Sentences with 以来/LC or 来/LC should match whether or not they have optional preceding elements like 自/P or 从/P. The grammar point typically appears at or near the start of a clause followed by a predicate describing what has happened since that time.

## 4-33
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Idiomatic phrases with 在 (zài) marking topic/aspect (在...上/下/中)
**Covers:** Sentences with a clause-initial 在 + Topic/Condition + 上/下/中, where the 在...上/下/中 phrase forms its own clause and expresses "in regards to," "on the topic of," "under [conditions]," or "in [circumstances]"
**Does not cover:** Location expressions (在 as simple preposition for place); temporal expressions (在...以前/以后, 在...内); action-in-progress (正在...); existence (在 marking location of subject); verbs followed by 在; 在于 "lies in"; 从...中 "from within"; phrases with 在...方面 (covered separately in 4-32); continuation 还在; assessment 在...看来; spatial relations 中间/当中; result complements with 下去; early expressions with 早在; inclusive 在内

```rule
match: ^ 在/P ... 上|下|中/LC $
unless: 早/AD 在/P
unless: 从/P
unless: /VV 在/P
unless: 以下/AD
unless: 以上/AD
unless: 在/P ... 看来/VV
unless: 在于/VV
unless: 在/P ... 内/P
unless: 包括 ... 在/P 内/LC
unless: 其中/NN
unless: 当中/LC
unless: 中间/NN
unless: 正在/AD
```

**Notes:** The earlier rule used `‖` in the pattern, which is not valid syntax, so it never matched. The pattern now anchors with `^` and `$`. It requires a clause that begins with 在 and ends with the localizer 上/下/中, with any noun phrase in between. That is the shape of the topic or condition clause in "在这种情况下，…", "在他的影响下，…" and "在这篇课文中，…". Because the clause must end on the localizer, ordinary spatial uses such as "在桌子上放着…" (where a verb follows in the same clause) are not matched. The "unless" lines exclude the look-alike points: 早在, 从, verbs taking 在, 在…看来, 在于, 在…内, 包括…在内, 其中, 当中, 中间, 正在, and 以上/以下.

## 4-52
<!-- written by claude-haiku-4-5 on 2026-11-09; edit freely -->
**Point:** Using 万一 to express "what if" or "in case" for unlikely/undesirable possibilities
**Covers:** 万一 introducing a hypothetical scenario (as adverb AD), followed by a consequence or question; includes 以防万一 idiom
**Does not cover:** Sentences where 就 appears without 万一; sentences using other conditional markers (要是, 假如, 一旦, etc.); other uses of 就 as aspect, time marker, or copula

```rule
match: 万一/AD
unless: ^ ... 万一/AD ... $ (this catches single-clause patterns that aren't proper conditionals)
```

**Notes:** The core pattern is simply detecting 万一 tagged as AD (adverb). The 万一 construction naturally expresses the target grammar point—it introduces an unlikely/undesirable scenario. The conditional response (with optional 就) follows in the same or next clause. The pattern is minimal because 万一 itself is the distinctive marker of this point. The 以防万一 idiom also counts as an exemplar of the point (expressing "in case" precaution). Single-clause sentences with only 万一 (no consequence) are excluded to avoid borderline cases, though question forms like "万一...呢?" will still match as they appear in multi-clause tokenization.

## 4-58
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** Expressing purpose with 好 (hǎo), meaning "in order to" or "so that"

**Covers:** Sentences of the form [Action/Clause]，好 + [Verb Phrase expressing purpose]. Here 好 is an adverb (AD) that opens the purpose clause. It may follow a personal-pronoun subject (我们好做笔记) or 这样 (这样好让客户满意). It may also follow a comma directly (好让…, 好考…). The tagger sometimes mis-segments 好听得懂 as 好听/VA 得 懂, and that case is covered.

**Does not cover:** 最好, 好像, 只好, 好(不)容易, 好 as "easy" (很好写), 好 as adjective or result complement (定好了), 还好, 正好, 不知道…好, and 好 in tag questions (好吗).

```rule
match: ^ 好/AD /VV _
match: ^ 我们|你们|他们|咱们|大家|我|你|他|她|人们/PN 好/AD /VV _
match: 这样/AD 好/AD /VV _
match: ^ 好听/VA 得/DER 懂/VV
unless: 最好/AD
unless: 最好
unless: 好像/AD
unless: 好像
unless: 只好/AD
unless: 只好
unless: 好/AD 不/AD 容易/VA
unless: 好不容易
unless: 好容易
unless: 不/AD 知道/VV
unless: 还好
unless: 正好
unless: 好/VA 吗/SP
unless: 好/AD 吗/SP
unless: 很|太|真|挺|非常|最|比较|更|特别/AD 好/AD
unless: 好/AD 几/CD
```

**Notes:** 好/AD must start a clause, follow 这样, or follow a personal-pronoun subject, and be followed directly by a verb and then more material. This captures the purposive "so that / in order to" reading. Degree adverbs before 好 (很好写) and the fixed compounds listed above are excluded to avoid the "easy to V" reading and other 好 grammar points.

## 4-73
<!-- written by claude-sonnet-5-5 on 2026-11-09; edit freely -->
**Point:** 还 used for surprise, sarcasm or a rhetorical question: 还 + noun/verb phrase + 呢 ("What a ...!"), or 还 + verb + 吗 as a rhetorical question.

**Covers:**
- Clause-initial 还 + phrase + 呢, which mocks a claim ("还留过学呢，英语这么差").
- 还 + verb phrase + 呢 after a pronoun subject ("你还生气呢").
- Pronoun subject + 还 + verb + 吗 ("你还听吗", "这种人你还相信吗", "过期的牛奶你还喝吗", "今天这么热，你还出门吗").
- A clause-initial 还 ... 吗 ("还在乎这点钱吗").
- 还真 + adjective ("这个小孩还真聪明").

**Does not cover:**
- 还好 (luckily), 还是 (or / had better / still), 还有 (also / there remain), and 还 meaning "also" or "still" (还在, 还没, 还不). None of these is the sarcastic or rhetorical use.
- 还 ... 可以/不错, and comparisons with 比 ... 还.
- "你还好吗？" (a plain "are you OK?"), "时间还够吗？" (a plain question with a noun subject), and other neutral questions.

```rule
match: ^ 还/AD ... 呢/SP $
match: ^ 还/AD ... 吗/SP $
match: /PN 还/AD ... 吗/SP
match: /PN 还/AD ... 呢/SP
match: 还/AD 真/AD
unless: 还/AD 在/AD
unless: 还/AD 没|不/AD
unless: 还/AD 好/VA 吗/SP
unless: 还/AD 可以|不错
unless: 比/P
unless: 不然
unless: 还是
unless: 还/AD 有/VE
```

**Notes:**
- The earlier rule wrote `PN` without a leading slash, so it was read as the literal word "PN" and the two pronoun-subject patterns never matched. They now use `/PN`, which restores "你还生气呢" and the 你 + 还 + verb + 吗 questions.
- The pattern is high-precision and low-recall. It misses sentences with a noun subject and 呢 (e.g. "他还在睡呢"), and ones with 还 + adjective + 呢 after a noun subject.
- "还 + 有/VE" is excluded outright to avoid 还有 (also / remaining). This also drops sarcastic cases like "还有钱呢".
- "还 + 真" can occasionally appear in neutral statements; this is accepted.
- Tagger quirks: 还 can be tagged CC, and 在 as AD or VV. The 在/AD exclusion relies on the tag. 还在乎 (在 tagged VV) is allowed through.
- 呢 as a continuing-state particle ("他还没起来呢") is excluded by the 还 没|不 exclusion.
