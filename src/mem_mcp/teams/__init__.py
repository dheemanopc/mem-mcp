"""Teams foundation — DAG membership + roles per memsys 1c47bd99.

Modules:
- dag — advisory lock, walk_ancestors/descendants, cycle prevention, max-depth-5
- roles — roles_catalog accessor
- assignments — assign_role / remove_member with sole-owner protection
"""

from __future__ import annotations
