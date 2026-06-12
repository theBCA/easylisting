---
name: pre-deploy
description: Run before pushing to Railway. Checks tests, changelog, import cycles, and obvious config issues.
---

## Pre-deploy checklist

**Test suite:**
!`cd web && python -m pytest -m "not integration" -q 2>&1 | tail -5`

**Unpushed commits:**
!`git log --oneline origin/main..HEAD`

**Changelog has an entry for recent changes:**
!`head -20 CHANGELOG.md`

**Import cycle check (apis must not import app):**
!`grep -rn "from app import\|import app" web/apis/ web/core/ 2>/dev/null || echo "Clean"`

**safe_error() not used for logging (only for client responses):**
!`grep -n "logger.*safe_error\|log.*safe_error" web/apis/ web/core/ 2>/dev/null || echo "Clean"`

**Stripe webhook route has both @csrf.exempt and @limiter.exempt:**
!`grep -A3 "stripe/webhook" web/apis/payments.py | grep -E "csrf|limiter|exempt"`

**Environment variables defined (check .env.example covers new vars):**
!`diff <(grep "^[A-Z]" .env.example | cut -d= -f1 | sort) <(grep "^[A-Z_]*=" .env | cut -d= -f1 | sort) 2>/dev/null | head -10 || echo "(no .env.example diff available)"`

If any check fails, stop and fix before deploying. Report what failed and why.
