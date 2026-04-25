# Help Train "Hey Rex" — Voice Sample Contribution Guide

Hey! Someone asked you to help train a voice assistant called REX so it recognizes "hey rex" reliably. You don't need to know any code. The whole thing takes about **30 minutes**.

There are 3 steps:

1. **Install** REX (one command).
2. **Record** ~100 short samples of yourself saying "hey rex" (~15 minutes).
3. **Send** the resulting .zip file back to whoever asked.

That's it. Here's exactly how to do each step.

---

## What you'll be doing, plain English

A small program will pop up on your screen. It will:

- Show you `[ 1 / 100 ]` and play a little tone.
- Wait ~2 seconds for you to say **"hey rex"** out loud.
- Say "saved" and move on to `[ 2 / 100 ]`.

You'll do that 100 times. Vary how you say it — some loud, some quiet, some fast, some slow, some leaning back from the mic. The variety is what makes the trained model good. **Don't worry about being perfect — natural is better than rehearsed.**

At the end, one command produces a single `.zip` file you send back.

---

## What gets recorded and where it goes

- The program records **only when prompted** — about 2 seconds at a time, 100 times. It's not always-listening.
- Your recordings are saved on your own computer first, in a folder called `~/.rex/wake_training/recordings/<your name>/`.
- The `.zip` file you send contains your audio clips and a small text file describing them (sample count, microphone name, your OS). **Nothing else** — no other files from your computer.
- Once the model is trained, the trained file is ~1 MB and **does not contain your voice** — it's a math model. The original recordings stay on the trainer's disk and aren't redistributed.
- If you change your mind later, just tell the person who asked and they'll delete your samples.

---

## Step 1 — Install REX (5 minutes, one command)

You need **Python 3.10 or newer** installed. Most modern Windows machines have it. To check:

Open **PowerShell** (press the Windows key, type "PowerShell", hit Enter) and run:

```powershell
python --version
```

If you see `Python 3.10.x`, `Python 3.11.x`, or `Python 3.12.x`, you're good. If it says "not recognized" or shows an older version, install Python from <https://python.org/downloads> first (check "Add Python to PATH" during install).

### Install REX with `pipx`

`pipx` is a tool that installs apps cleanly. Run these two commands in PowerShell:

```powershell
python -m pip install --user pipx
python -m pipx ensurepath
```

**Close PowerShell and reopen it** (this is important — it picks up the new PATH).

Then:

```powershell
pipx install rex-voice-assistant
```

This downloads REX and its dependencies (~200 MB, takes 1–2 minutes). When it's done you should be able to run:

```powershell
rex --version
```

and see a version number. If you get "rex is not recognized," close PowerShell, reopen, and try again. If still broken, tell your friend — there's a fallback option.

---

## Step 2 — Record your samples (15 minutes)

In PowerShell:

```powershell
rex record-wake-samples
```

It will ask:

```
Your name (used to label your recordings) [your-username]:
```

Type your **first name** (no spaces, no special characters — `alex`, `jordan`, `sam` etc.) and hit Enter. This is just a label so the trainer knows which samples are yours.

Then it shows a panel with the plan and asks `Ready to start?` — say `y`.

### What to do during recording

You'll see this 100 times:

```
[  1 / 100 ]  next file: hey_rex_001.wav
  Get ready... say 'hey rex' when you hear the tone
  *ding*
  Recording...
  Saved (peak=0.45, rms=0.082)
```

When you hear the tone, **say "hey rex"** in a normal voice. After it says "Saved," the next prompt comes within a couple seconds.

### Tips for good recordings

The whole point is **variety**. The trainer is going to mix your samples with other people's, and the model gets better the more varied each person's batch is. So:

- **Don't say it the same way every time.** That actually hurts the model.
- **Move around.** Some close to the mic, some leaning back, some across the room.
- **Vary loudness.** Most normal, a few quiet, a few louder. Maybe one or two whispered, one or two slightly shouted.
- **Vary pace.** Fast, slow, drawn-out, snappy.
- **Vary mood.** Bored, cheerful, tired, annoyed, neutral.
- **Background noise is fine** — fan, AC, music quietly playing, even other people talking softly. It actually helps.

### What if I mess up a recording?

If you cough, get cut off, or mumble — just keep going. The tool **automatically rejects** clips that are too quiet or clipped, and asks you to retry that slot. Bad samples don't make it into the final batch.

If you say something wrong (like "hey jarvis" out of habit) and it doesn't reject — that's fine, the trainer will spot-check before merging. Don't worry about it.

### Taking a break

You can stop anytime with **Ctrl+C**. Run `rex record-wake-samples` again later and it'll resume where you left off (it auto-numbers, so you'll continue from sample 47 or wherever). Just enter the same name when prompted.

### When you're done

After 100 samples, you'll see a green panel saying "Session complete" with the path to your recordings. Move on to Step 3.

---

## Step 3 — Package and send (2 minutes)

In PowerShell:

```powershell
rex package-wake-samples
```

It auto-detects your folder, scans the recordings, and produces a single `.zip` file. You'll see something like:

```
Package ready

Contributor: alex
Samples: 100
Size: 4.2 MB

File:
  C:\Users\alex\.rex\wake_training\alex_hey_rex_20260425.zip

Send this single .zip to the person who asked you to record
(Discord, email, Drive — whatever's easiest).
```

The `File:` line tells you exactly where the .zip is on your computer. **Send that file** to the person who asked you to do this.

You can also navigate to it in File Explorer:

1. Press Windows+R
2. Type `%USERPROFILE%\.rex\wake_training` and hit Enter
3. The .zip file is right there — drag it into Discord/email/wherever.

---

## After you send

You're done! Feel free to delete REX if you don't want to keep it (`pipx uninstall rex-voice-assistant`). The .zip on your disk can also be deleted once you've sent it.

If the trainer comes back and asks for **a few more samples** (sometimes happens if certain conditions weren't covered), just run `rex record-wake-samples --count 30` again, then `rex package-wake-samples` again, and send the new .zip. The auto-numbering means it won't overwrite anything.

---

## Common issues

**"rex is not recognized"** — Close PowerShell completely, reopen it, try again. If still broken, run `pipx ensurepath` once more and reopen.

**"Audio callback status: input overflow"** — Your computer's a little slow but the recording will still work. Ignore it.

**The tone doesn't play / I can't hear it** — The recordings still work. Just say "hey rex" about 0.5 seconds after the `Recording...` line appears.

**"No audio input detected"** — Your microphone isn't being picked up. Check Windows sound settings, make sure the right mic is the default, and try again.

**Tool keeps rejecting my recordings as "too quiet"** — Move closer to the mic, or check that Windows isn't muting your input. You can also pass `--device "Microphone Name"` if the wrong mic is being picked.

**I want to stop after 50** — Just hit Ctrl+C. 50 is plenty if your samples are varied. Run `rex package-wake-samples` and send what you have. Tell the person you sent fewer.

**Can someone else hear me through the recordings?** — Only the person you send the .zip to. The recordings never leave your computer until you send them. They're not uploaded anywhere automatically.

---

Thanks for helping! It honestly makes a big difference — a model trained on multiple voices is dramatically more robust than one trained on a single person.
