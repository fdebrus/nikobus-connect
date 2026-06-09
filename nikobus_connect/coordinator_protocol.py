"""The formal contract between this library and its host coordinator.

The library is driven by a *coordinator* object owned by the host
application (the Home Assistant integration in practice). Historically
that contract was implicit — scattered ``getattr(coordinator, ...)``
calls that silently skipped features when a member was missing, and
cross-object attribute writes documented only in comments. This
Protocol makes it explicit and ``mypy``-checkable on both sides:

* the **library** type-checks against exactly this surface (no silent
  feature skips on typos), and
* the **host** can declare ``CoordinatorProtocol`` conformance and have
  mypy verify it implements everything the library will touch.

Attributes in the *written by the library* group are assigned by
discovery during a scan (the host seeds them and may read them for
progress/diagnostics); the rest is read-only from the library's side.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .command import NikobusCommandHandler
    from .discovery import InventoryQueryType


class CoordinatorProtocol(Protocol):
    """Host-coordinator surface the library reads and writes."""

    # --- Written by the library during discovery -------------------------
    #: True while any discovery phase is running.
    discovery_running: bool
    #: Truthy while a per-module register scan is targeting a module.
    discovery_module: Any
    #: Address of the module currently being register-scanned.
    discovery_module_address: str | None
    #: PC_LINK / MODULE phase marker, read by the host's frame routing.
    inventory_query_type: InventoryQueryType | None

    # --- Read by the library ---------------------------------------------
    #: Module store grouped by ``module_type`` (the scan planner's input).
    dict_module_data: dict[str, Any]
    #: The command pipeline, used for inventory queries and probes.
    nikobus_command: NikobusCommandHandler | None

    def get_module_type(self, module_id: str) -> str | None:
        """Hardware type of a module address, from the host's store."""
        ...

    def get_module_channel_count(self, module_id: str) -> int:
        """Channel count of a module address, from the host's store."""
        ...

    def get_button_channels(self, button_address: str) -> int | None:
        """Operation-point count of a physical button address, or None."""
        ...
