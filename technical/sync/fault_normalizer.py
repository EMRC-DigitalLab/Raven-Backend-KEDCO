"""
technical/sync/fault_normalizer.py

Intelligent fault type normalizer: DataNest raw string → Raven interruption_type code.

Resolution order:
  1. Exact match against FaultTypeCategory codes
  2. Case-insensitive match against FaultTypeCategory codes
  3. Known DataNest quirks / aliases (hardcoded corrections)
  4. Slash/space normalization + retry steps 1 & 2
  5. Store raw value as-is (NEVER N/A) — unknown codes are logged
     so they can be reviewed and added to FaultTypeCategory in admin.

The normalizer is built once per sync run (cached from DB) so it doesn't
hit FaultTypeCategory on every single row.
"""

import re

# ── Known DataNest quirks ─────────────────────────────────────────────────────
# Raw string DataNest sends → correct Raven code
# Add new entries here as you discover mismatches.
_KNOWN_ALIASES = {
    # Case variants
    'ls':          'L/S',
    'LS':          'L/S',
    'e/f':         'E/F',
    'o/c':         'O/C',
    'tcn':         'tcn',
    'TCN':         'tcn',
    'fault':       'fault',
    'FAULT':       'fault',
    'permit':      'permit',
    'PERMIT':      'permit',
    # String differences
    'ON':          'O/N',
    # L/F is DisCo Line Fault — do NOT remap to 132KV L/F (TCN fault)
    # 'L/F':       '132KV L/F',  ← removed: was incorrectly upgrading DisCo faults to TCN
    'OC & E/F':    'O/C & E/F',
    '132 KV E/F':  '132KV E/F',
    '132 KV L/F':  '132KV L/F',
    '132 KV CB/F': '132KV CB/F',
    '330 KV L/F':  '330KV L/F',
    '330 KV L/S':  '330KV L/S',
    '132KV MTNC':  '132KV MTCE',
    'MAINTENANCE': 'MTNC',
    'LOAD SHEDDING': 'L/S',
    'LOADSHEDDING':  'L/S',
    'LOAD SHED':     'L/S',
}

# All valid Raven codes from FeederInterruption.INTERRUPTION_TYPES
_RAVEN_CODES = {
    'E/F', 'O/C', 'O/C & E/F', 'NO RI', 'N/A', 'L/S', 'O/S', 'T/F',
    'B/F', 'O/N', 'O/E', 'P/O', 'O/F', 'P/M', 'O', 'T/S', 'L/S GS', 'L/F',
    'MTNC', 'OC & E/F', 'EM/D', '330KV L/F', 'OFF', 'S/C', '132KV E/F',
    '132KV L/F', '330KV L/S', '132KV CB/F', 'D/C', 'MTCE', 'IN O/C',
    'T/LS', '132KV MTCE', 'LIM', 'tcn', 'fault', 'permit',
}


class FaultNormalizer:
    """
    Built once per sync run. Call normalise(raw) for each DataNest fault_type.
    Tracks unrecognised codes so the caller can log them.
    """

    def __init__(self):
        # Load FaultTypeCategory into a case-insensitive lookup
        from technical.models import FaultTypeCategory
        self._cat_exact = {}       # exact code → code
        self._cat_upper = {}       # upper() → code

        for obj in FaultTypeCategory.objects.all():
            code = obj.code.strip()
            self._cat_exact[code] = code
            self._cat_upper[code.upper()] = code

        # Also seed from the hardcoded RAVEN_CODES set (covers any gap)
        for code in _RAVEN_CODES:
            self._cat_exact.setdefault(code, code)
            self._cat_upper.setdefault(code.upper(), code)

        self.unrecognised = set()   # raw codes that fell through to step 5

    def normalise(self, raw) -> str:
        if not raw:
            return 'N/A'

        raw = str(raw).strip()
        if not raw:
            return 'N/A'

        # Step 1 — exact match
        if raw in self._cat_exact:
            return self._cat_exact[raw]

        # Step 2 — case-insensitive match
        upper = raw.upper()
        if upper in self._cat_upper:
            return self._cat_upper[upper]

        # Step 3 — known aliases
        if raw in _KNOWN_ALIASES:
            return _KNOWN_ALIASES[raw]
        if upper in {k.upper(): v for k, v in _KNOWN_ALIASES.items()}:
            for k, v in _KNOWN_ALIASES.items():
                if k.upper() == upper:
                    return v

        # Step 4 — normalise spacing/slashes and retry
        cleaned = re.sub(r'\s+', ' ', raw).strip()          # collapse whitespace
        cleaned_upper = cleaned.upper()
        if cleaned in self._cat_exact:
            return self._cat_exact[cleaned]
        if cleaned_upper in self._cat_upper:
            return self._cat_upper[cleaned_upper]
        if cleaned in _KNOWN_ALIASES:
            return _KNOWN_ALIASES[cleaned]

        # Step 5 — unknown: store raw, log for admin review
        # Truncate to field max_length (100) just in case
        result = raw[:100]
        self.unrecognised.add(raw)
        return result
