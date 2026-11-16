# Q&A bank

The panel is CS leadership, Sales leadership or an AE, an SE leader, and marketing or a founder. The backup slides already cover the technical objections; this bank covers the questions those roles are likely to ask.

Each answer is built for about 30 seconds: say the headline first, then one or two support points, then stop. Backup slide numbers refer to `deck/deck.md`.

## How to take a question

1. **Restate it in one line.** It buys three seconds and makes sure the whole room heard it.
2. **Headline first.** One sentence that answers the question. Most people stop listening after it, so it has to stand alone.
3. **One proof point.** A number from the demo, an allowlisting analogy, or a backup slide. Not all three.
4. **Check and stop.** "Does that get at it?" Then wait.
5. **If you don't know,** say so and say how you'd find out. A confident "I don't know, here's how I'd check" scores better than a guess on a panel.

Stay in your own voice. If someone asks how a specific vendor does it, answer about the category and your view; don't speak for any product.

## CS leadership

### "Won't default deny slow customers down and hurt adoption?"

**"Yes, if the exception path is slow. That's the real risk, so it's the metric I'd manage."**

- Learn mode first: the baseline covers what the agent already does, so day one blocks very little.
- Exceptions are scoped, justified and expire. Exception turnaround time is the CS signal on slide 9.
- The whitelisting lesson: when approvals took a week, people routed around the control. Rollouts that stuck kept the exception path fast.

Backup: slide 16, "Friction kills adoption".

### "What do the first 90 days look like for a new customer?"

**"Start with one agent that can write or send, and walk it through the four steps on slide 9."**

- Discover: inventory agents, tools and MCP servers; run learn mode on that one agent.
- Govern: security and the agent's business owner sign the policy together.
- Protect: enforce writes and sends first; trusted reads stay open.
- Prove: bring the audit numbers to the first QBR, including blocked attempts and approval turnaround.

Then expand agent by agent. Don't try to baseline everything before enforcing anything.

### "How would you know a customer is healthy or at risk?"

**"Healthy means policy is enforced and exceptions are fast. At risk means stuck in monitor mode, or exceptions widened until they mean nothing."**

- Healthy signals: share of agents with a baseline, time to first approved policy, exception turnaround, enforcement still on.
- Warning signs: no new agents onboarded in a quarter, exceptions with no expiry, a single "allow all" rule to stop complaints.
- Same pattern as whitelisting: the value was there only once enforcement was on.

### "An agent got blocked mid-quarter and the business owner is angry. What do you do?"

**"Treat it as the control working, then fix the friction fast."**

- First, the audit log: what was the agent trying to do, and was it legitimate?
- If legitimate: a scoped, time-bound exception the same day, and a baseline update so it doesn't recur.
- If not: that's the QBR story. Show the business owner the attempt that was stopped.
- Either way, the turnaround time is what they'll remember.

### "What would you do differently from classic application allowlisting?"

**"Make the exception path the product, not an afterthought."**

- The technology worked. Where it struggled was maintenance: rulesets and exceptions administered by hand.
- For agents: learn mode drafts the baseline, exceptions carry a justification and an expiry, and the policy is versioned like code.
- SOAR taught the same lesson: playbooks paid off only when someone kept them maintained.

## Sales leadership and AEs

### "What's the one-line pitch?"

**"You can't make the model unpoisonable, so control what it's allowed to do."**

Follow-up if they want the analogy: "We stopped trying to list every bad program and started allowing only the good ones. Same move, applied to agents."

### "The CISO already bought a guardrails product. How do I position this?"

**"Don't replace it. Layer on it: guardrails lower how often the agent is fooled, runtime control caps what it costs when it is."**

- The demo: the hardened prompt still sent the contact list, 20 out of 20 on Haiku 4.5.
- Guardrails are detectors, and detectors catch what they recognize. Runtime control is deterministic code outside the model.
- The CISO question that opens the door: "If the guardrail misses, what stops the send?"

Backup: slide 15, "Wasn't the hardened prompt a straw man?"

### "Who's the buyer: security, or the team building the agents?"

**"Security owns the policy, but the champion is often the team that wants to ship agents."**

- Agents that can write or send usually stall in security review. Runtime control is what gets them approved for production.
- The business owner co-signs the policy, which is why Govern on slide 9 names both.
- So the pitch to the agent team is speed: "this is how you get to yes."

### "Why now? What's the trigger in an account?"

**"The moment agents go from reading to doing: sending email, updating CRM, calling third-party tools."**

- Read-only copilots were mostly a data-access question. Write and send access is where one poisoned doc becomes an incident.
- MCP makes it easy for anyone to connect new tools, often faster than security can review them.
- Good discovery question: "Which of your agents can write or send today?" It's the first of the three on the close slide.

### "How do you handle 'we'll just use the newest model'?"

**"Newer models are better, and the demo shows it. They still fall for the next attack."**

- Haiku 5.5 caught the disguised doc 20 out of 20.
- The same model sent the full contact list to a poisoned third-party tool 20 out of 20. Default deny blocked it 40 out of 40 across both models.
- Customers' deployed agents run whatever model they shipped with.

Backup: slide 12 (run the encore if there's time; about 90 seconds).

## SE leader

### "What does a proof of value look like?"

**"Two to four weeks on one real agent, with success criteria agreed on day one."**

- Week 1: inventory and learn mode on an agent that can write or send.
- Week 2: the business owner reviews the drafted baseline; enforce it in a test environment.
- Then replay a known injection, like the demo's poisoned doc, and show the block plus the audit entry.
- Success criteria: baseline coverage, false blocks per week, exception turnaround.

### "Isn't this just allowlisting? Won't agents need thousands of rules?"

**"It is allowlisting, on purpose. The trick is choosing the right unit."**

- Rules are written on action classes (read, create, update, delete, send) and resource scopes, not on prompts or individual calls.
- Learn mode drafts them and generalizes reads across accounts. The demo's baseline is a handful of rules.
- Same as endpoint allowlisting: you trusted publishers and paths, not every single file hash.

Backup: slide 16, "Agents aren't static binaries".

### "Where does enforcement actually run? What about latency?"

**"Wherever the tool call crosses a boundary you control: the agent framework, an MCP gateway, or the platform's hooks."**

- In the demo it wraps the agent's toolset, so every call goes through policy before it executes. `docs/ARCHITECTURE.md` walks through it.
- The policy decision is a rule lookup, not a model call, so it adds far less latency than the model itself.
- Production adds what the demo simplifies: enforcement outside the agent process, verified tool identity, and argument checks on reads.

Don't quote latency numbers; say "I'd measure it in the proof of value."

### "MCP servers are admin-approved already. Isn't that enough?"

**"Approval decides what's installed. Runtime decides what it does."**

- Approval is per server, once, at install. The agent then inherits everything the user can do.
- The protocol handles authentication well but doesn't tie a tool or action to a permission.

Backup: slide 13. If they want proof, slide 12 or the live encore.

### "I already approved that MCP tool. Why block it again?"

**"You approved a name. After the rugpull, it's a different program."**

- Same tool name (`enrich_contacts`), new description that asks for the full contact list.
- Allow rules pin a SHA-256 of the advertised definition at approval. Drift is default deny; a fresh exception is required for the new hash.
- Same shape as Bit9 / application allowlisting: we didn't trust the filename, we trusted the hash.

Backup: MCP rugpull encore in `docs/DEMO_SCRIPT.md` (scenario key `-`). Slide after the poisoned-tool Q&A. Live screenshots of that run are the last backup slide.

### "What can default deny not stop?"

**"Data leaking through a channel you've already approved. That's the honest gap."**

- A summary written into an approved shared doc can carry data out.
- Read-only isn't automatically safe: a fetch tool can exfiltrate through URLs.
- What closes it: least privilege per resource, content checks on writes, and agent identity as the next layer.

Backup: slide 16. Volunteering a limit builds more trust than defending a perfect story.

## Marketing and founders

### "How would you explain this to a non-technical executive?"

**"We used to try to list every bad program. Then we flipped it and only allowed the good ones. Agents need the same flip."**

Then one sentence from the demo: "An agent read a poisoned doc and tried to email our customer list to a stranger. The model didn't stop it. A rule did."

### "Where does this market settle, and what's defensible?"

**"Guardrails at the model become a bundled commodity. The durable layer controls what agents can do across every model and platform."**

- Customers will run many models and agent platforms at once, so control has to sit across them.
- That's inventory, identity, policy and runtime enforcement: the control plane on slide 8.

### "What message would land with a CS or customer audience?"

**"The three questions from the close slide. They're easy to remember and most teams can't answer them yet."**

- Which agents can write or send today?
- Who approved that?
- Would you know if one did something it shouldn't?

For CS specifically: blocked attempts in the audit log are a QBR story. Value you can show beats risk you have to imagine.

### "What's the weakest part of your argument?"

**"Operational cost. Default deny works, but someone has to maintain the policy, and if exceptions are slow, people route around it."**

- That's why learn mode, scoped exceptions with expiry and exception turnaround are the core of the talk, not side notes.
- And it doesn't cover approved-channel leaks on its own (slide 16).

## Curveballs

| If they ask | Short answer |
|---|---|
| "Is the demo rigged? Why Haiku?" | "Small fast models are what teams put on agent loops. Every cell ran 20 times; the measured numbers are on slides 12 and 15." |
| "How does this compare to [vendor]?" | "I kept this vendor-neutral on purpose. Here's how I'd evaluate any of them: does it control the action, or only filter the prompt?" |
| "What did you learn building this?" | "The newer model caught the disguised doc every time, then handed the contact list to a poisoned tool every time. Each fix covers the last attack, not the next one." |
| "What would you build next?" | "Agent identity: scoped, expiring credentials per agent, so the policy knows which agent is acting, not just which user." |
| "How long did this take?" | Give the honest answer, then pivot: "The policy engine was the easy part. Making the attack reliable was the hard part." |
