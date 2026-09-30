"""Remove impossible quote ticks (yes>=90 and no>=90) from stored data."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pmanalysis.db.cleanup import remove_impossible_quote_ticks


def main() -> None:
    result = remove_impossible_quote_ticks()
    print("Removed impossible quote data:")
    print(f"  ticks:             {result['ticks_removed']}")
    print(f"  indicators:        {result['indicators_removed']}")
    print(f"  windows affected:  {result['windows_affected']}")


if __name__ == "__main__":
    main()
