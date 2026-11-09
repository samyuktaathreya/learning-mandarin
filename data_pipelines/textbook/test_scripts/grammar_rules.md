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
<!-- written by claude-sonnet-5-5 on 2026-11-07; edit freely -->
**Point:** The aspect particle 过 after a verb says the subject has (or has never) had an experience, as in 去过中国 and 没学过这个词.

**Covers:** A verb followed by 过 as an aspect particle, in affirmative, negative (没), or question (吗) sentences, with or without an object. Examples: 我也吃过日本菜, 我们都没坐过飞机, 你学过中文吗. It also covers 见过, which the tagger sometimes tags 见/VV 过/VV.

**Does not cover:**
- 经过, 通过, 不过, 莫过于, 超过, 过于, 过年, 过去 and 过来. These are single words or other structures, so a bare 过 never matches inside them.
- Direction-complement uses such as 走过来 and 搬过去.
- Motion-past uses such as 走过这条街, which describe passing through a place, not an experience. Verbs of this kind (走, 跑, 飞, 游, 跳) are excluded.
- Completion uses where 过 is followed by 了, such as 吃过饭了. These are borderline, so they are excluded.

```rule
match: /VV 过/AS
match: 见 过/VV
unless: 走|跑|飞|游|跳 过
unless: 过/AS ... 了
unless: 过/AS 了
```

**Notes:**
- The main pattern needs a VV directly before an AS-tagged 过. The only VV-tagged 过 it allows is the one after 见, because the tagger gets that example wrong. Allowing 过/VV after other verbs would let in 走过来 and 过上 (as in 过上好日子).
- The unless lines are deliberately conservative to favor precision. Some real experience sentences, such as 我走过这条路, are lost.
- Unless lines are checked over the whole clause, so any 了 after 过 in the same clause blocks the match.

## 3-41
<!-- written by claude-sonnet-5-5 on 2026-11-07; edit freely -->
**Point:** 一 + measure word (+ noun) + 也/都 + 不/没 + verb, which emphasizes "not even one" (一个人都没有, 一口也没吃).
**Covers:** Clauses with 一 (CD) directly followed by a measure word, an optional noun, then 也 or 都 (AD), then 不 or 没 (AD) or 没有 (VE/AD). The noun may be absent (我一口都没吃). Topicalized objects before the subject are also fine, because the pattern only looks at the 一…也/都…不/没 stretch.
**Does not cover:** 一 + MW with no negation, such as 他得了一个大奖. Also excluded: 什么/谁/哪儿 + 都, 再也不/没, 连…都, 都 meaning "already" or "all" without 一 + MW, and a bare 也/都 + 没有 with no 一 + MW before it. These are other points.

```rule
match: 一/CD _/M 也|都/AD 不|没/AD
match: 一/CD _/M 也|都/AD 没有|没/VE
match: 一/CD _/M 也|都/AD 没有/AD
match: 一/CD _/M _/NN 也|都/AD 不|没/AD
match: 一/CD _/M _/NN 也|都/AD 没有/VE
match: 一/CD _/M _/NN 也|都/AD 没有/AD
match: 一/CD _/M _/NN _/NN 也|都/AD 不|没/AD
match: 一/CD _/M _/NN _/NN 也|都/AD 没有/VE
match: 一/CD _/M _/JJ _/NN 也|都/AD 不|没/AD
match: 一/CD _/M _/JJ _/NN 也|都/AD 没有/VE
unless: 连 ...
unless: 再 也/AD
unless: 何况
unless: 不管
unless: 无论
```

**Notes:** Each match line requires 一 (CD), then a measure word, then optionally one or two nouns or a JJ+NN, and then 也/都 followed immediately by a negator, so ordinary 一 + MW + noun objects don't fire. Negators tagged 没有/VE or 没有/AD (the tagger sometimes mislabels it) are handled in separate lines. The unless lines drop 连…都 (a different point), 再也, 何况 and 不管/无论 sentences, which are borderline. The ordinary 一 + MW sequences (一个人, 一次) cannot match without 也/都 plus a negator after them. If 一点 is tagged M it is covered (一点消息都不知道). If a tagger gives 一点 as AD or something else, it will be missed, which is acceptable.

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
