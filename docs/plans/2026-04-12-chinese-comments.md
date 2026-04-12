# Chinese Comments Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make project-owned code comments default to Chinese without changing runtime behavior.

**Architecture:** Treat this as a text-only maintenance pass. Translate explanatory comments in `scripts/` and `frida_hooks/`, while preserving legal headers, upstream/vendored files, URLs, command examples, type-checker pragmas, and protocol literals.

**Tech Stack:** Python, JavaScript Frida hooks, Shell, `rg`, `py_compile`.

---

### Task 1: Scope Comment Pass

**Files:**
- Modify: `tasks/todo.md`
- Read: `scripts/**/*.py`
- Read: `scripts/**/*.js`
- Read: `frida_hooks/**/*.js`

**Step 1: Scan comment-bearing files**

Run: `rg -n "#|//|/\\*|\\*/" scripts frida_hooks -g "*.py" -g "*.js"`

**Step 2: Exclude risky comments**

Preserve legal headers, external references, type-ignore pragmas, regex/protocol examples, and URL-heavy examples.

### Task 2: Translate Project-Owned Comments

**Files:**
- Modify: `scripts/**/*.py`
- Modify: `scripts/**/*.js`
- Modify: `frida_hooks/**/*.js`

**Step 1: Translate comments only**

Keep code, identifiers, strings, and user-visible output stable.

**Step 2: Keep technical terms readable**

Preserve terms such as Frida, Hook, AES, DRM, Cronet, protobuf, MediaCodec, TTVideoEngine where they are the clearest domain language.

### Task 3: Verify

**Files:**
- Test: modified Python files
- Check: modified JavaScript files by comment scan

**Step 1: Run Python compile checks**

Run: `python -m py_compile <modified Python files>`

**Step 2: Run targeted tests where touched logic has existing tests**

Run: `python -m unittest tests.test_download_drama.FridaDeviceTests`

**Step 3: Scan remaining English comments**

Run: `rg -n "#|//|/\\*|\\*/" scripts frida_hooks -g "*.py" -g "*.js"`

Expected: Remaining English is limited to preserved technical strings, examples, or upstream/legal material.
