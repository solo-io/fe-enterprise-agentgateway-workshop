---
name: stop-slop
description: Use when you write, revise, or review prose and need to strip the patterns that mark it as AI-generated.
---

# Stop Slop

Slop announces, inflates, and decorates instead of stating. State the fact, name the actor, cut the ornament. Work through the checklist, then sweep for repeats.

## Words and phrases

- **Throat-clearing openers** ("Here's the thing:", "The truth is", "It turns out", a paragraph starting with "So"). Cut them; state the point.
- **Filler intensifiers and hedges** (really, truly, actually, genuinely, fundamentally, simply, deeply; "Full stop.", "Let that sink in"). Delete. Keep an adverb only when it changes meaning: "only", "early", "twice weekly" survive.
- **Vague declaratives** ("The impact is significant", "The root causes are systemic"). Say which impact or which cause, or cut the sentence.
- **Meta-commentary** ("The rest of this doc explains...", "As we'll see", "In this section, we'll"). Delete; let the document move.
- **Negated requirements** ("X needs no Y", "without needing Y"). State the positive fact: "The match alone is enough."
- **Inverted list filler** ("[list], X among them", "to name a few"). Say "including X" or list plainly.
- **Lazy extremes** (every, always, never, nobody) doing vague work. Use the specific: which ones, how often.
- **AI-default vocabulary** (delve, leverage, robust, seamless, meticulous, pivotal, crucial, tapestry, testament, foster, harness, myriad, game-changer, cutting-edge, ever-evolving) and formal-for-plain swaps (utilize, facilitate, prior to, in order to). Use the plain word: use, help, before, to. Keep a term the field actually requires.
- **Inflated copulas** ("serves as a", "stands as a", "represents a", "functions as a", "boasts/features a"). Write "is" or "has".
- **Vague attribution** ("experts say", "studies show", "many believe", "is widely regarded as"). Name the source, leave a bracketed prompt for it ("[cite the adoption study]"), or cut the claim. Don't launder it: dropping "experts say" and keeping the claim turns hearsay into your own assertion.
- **False ranges** ("from powering X to enabling Y, it does it all", "spanning everything from A to B"). Manufactured breadth. List the actual items as a plain sentence; keep a range only when the endpoints are real data.
- **Sequencing scaffolds** ("In conclusion", "In summary", "Firstly/Secondly", "Ultimately," as an opener). Cut the scaffold, state the point. End on the last real fact, decision, or consequence, never a paragraph that restates the opening or a stock forecast ("the future looks bright", "exciting times ahead").
- **Chatbot residue** ("Certainly!", "Great question", "I hope this helps", "as of my last knowledge update"). Assistant scaffolding pasted along with AI output. Delete on sight.

## Sentences and voice

- **Passive that hides the actor** ("mistakes were made", "the change was approved"). Name who acted and make them the subject.
- **False agency** (an inanimate noun doing what only a person can: "the strategy evolved", "the data tells us", "the roadmap decided"). Name the person; use "you" if no one specific fits.
- **Binary contrasts** ("Not X. Y.", "The problem isn't X, it's Y", "not only X but Y") and **negative listing** ("Not the cost. Not the timeline. The team."). State Y; drop the runway.
- **Rhetorical setups** (a question you answer in the next breath: "What if...?", "Think about it:", "Here's what I mean:"). Make the point; keep a question only when the reader would ask it unprompted.
- **Count announcements** ("Three points bear on X", "The report states X in two places"). Delete-test: if the list underneath still reads, the sentence was throat-clearing. Put the finding in that slot, or cut. Keep a count only when the number is the news ("Five of the six comparables have no basement").
- **Justification padding** ("[fact], so every other lab uses it" when nobody questioned it). Cut the "so" clause; if the remaining fact changes nothing for the reader, cut the sentence.
- **Narrator-from-a-distance** ("No one planned this", "People tend to"). Address the reader as "you" and show the moment: "You don't decide to let the config drift. You notice it a year later."
- **Trailing participle commentary** (", highlighting...", ", underscoring...", ", showcasing...", ", paving the way for..."). Fake analysis that restates the sentence it hangs off. Delete the clause; if the analysis is real, give it its own sentence with its own evidence.
- **Transition stacking** (sentence-initial "Moreover," "Furthermore," "Additionally," as decoration). Delete them. Keep a transition only when it names a real relationship: but, because, for example.

## Rhythm

- **Em dashes**: zero, no exceptions. Use commas, colons, or periods.
- **Dramatic fragments** ("Fast. Cheap. Reliable. Choose one." / "And that's the whole story.") and stacked punchy sentences. Write complete sentences; vary paragraph endings.
- **Same verb repeated for parallelism** ("survives... survives... survives"). Say it once, list the rest.
- **Synonym cycling**, the opposite failure ("the app... the platform... the solution" for one referent; "said... stated... noted... remarked"). Pick one name per thing and repeat it; forced variation distracts more than repetition.
- **Metronomic sentences**: three in a row of matching length. Break one.
- **Default triads**: three-item lists as a reflex. Use three when the content is three; don't pad or trim to hit a rhythm.

## Technical documents

- **Borrowed-significance vocabulary**: stock phrases that assert a thing matters instead of saying what depends on it. "load-bearing", "doing the work", "earns its place", "carries weight", "is the point", "is the tell", "not decoration", "non-trivial", "first-class". Name the dependency and the failure: "`init-config` is the only one, and the pod does not start without it" beats "`init-config` is load-bearing." These read as AI writing even when the underlying claim is true, so replace them rather than varying them.
- **Stakes-inflation metaphor** ("an egress hole with a friendly name", "a region where prompts come to rest"). State the literal consequence: what breaks, what it costs, who is exposed.
- **Negative framing**: describing a thing by what it is not or by what the system lacks ("a chart this repo does not vendor", "config that lives nowhere in this tree", "a dependency we don't ship"). Every negation implies a positive fact, so write that instead: the source, the location, the requirement. "You install vSR from the upstream chart." Keep the negation only when a reader would otherwise go looking for the thing, and then say where to look.
- **Table echo**: prose under a table re-reading its cells. Keep only the calculation, comparison, or consequence the table can't show.
- **Mechanism preamble**: prose narrating a code or config block line by line before the block. Cut the walkthrough; keep context the block can't show (why it exists, a caveat, a gotcha). A terse lead-in is enough: "The following ClusterPolicy blocks X:"
- **Callback motifs**: echoing a memorable earlier phrase as a refrain pages later. Restate the technical fact plainly each time.
- **Historical or internal-facing comments**: a comment that records what the code used to do, when something changed or was measured, which version fixed it, or rationale only a maintainer of this repo needs ("replaces the old X", "(fixed in v2.3)", "measured 2026-08-19"). Delete it, or cut it to the one fact a new reader setting up the happy path acts on. History belongs in git, design docs, or a changelog.
- **Bold sprinkle**: bold on every key term, or long runs of `**Label:** text` bullets. Reserve bold for the one thing a skimming reader must not miss. For label runs, drop the labels or fold each into its sentence; the labels are structure pretending to be emphasis.
- **Not slop**: Note/Warning/Tip callouts that carry a real caveat, even when they interrupt the flow. Cut the decoration around the fact, not the fact.

## Constrained registers

Legal filings, formal disputes, and similar documents must stay in third person; "you" and first person would wreck them. Third person that names both actors is fine: "The borrower asks that the appraiser supply the derivation." Passive that hides the actor is still slop: "Adjustments were made based on..." Cut the ornament, keep the register.

## Repairs

Fixing a tell must not damage the text:

- **Repairs come from the source.** Replace vague with specific only when the source supplies the specific ("results improved" → the actual number). Missing detail gets a bracketed prompt ("[actual latency figure]") or a question to the author; a bracket is honest, an invented detail is not.
- **Keep claim strength.** "May" stays "may", "suggests" stays "suggests". Cut ceremonial hedges that postpone the claim; keep genuine uncertainty.
- **No manufactured humanness.** No fake slang, staged fragments, planted imperfections, or sentence-length variation done to hit a rhythm target. Variation comes from meaning.
- **Genericity test** for a sentence that feels polished but empty: would it survive unchanged with the names and subject swapped out? If yes it's doing no work; cut it or rebuild it from source material.
- **Clean text stays clean.** A passage with no tells returns unchanged. Don't force edits to prove effort.

## Finishing

Fixed one tell? Sweep the whole document for its family before delivering. You wrote it in one voice, so a pattern that appears once tends to appear three or four more times.
