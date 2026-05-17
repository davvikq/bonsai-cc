"""Claude Code hook integration: installer, client script, doctor.

The user-facing pieces of this package are deliberately split:

* :mod:`bonsai_cc.hook.client_template` -- **the stable interface**.
  This file is shipped verbatim into ``<home>/hook_client.py`` at
  install time and called by Claude Code on every event. It is
  stdlib-only and fail-silent. See the module docstring for the
  complete contract.

* :mod:`bonsai_cc.hook.installer` -- the merge logic for
  ``settings.json``. Used only by ``bonsai-cc install-hook`` and
  ``bonsai-cc uninstall-hook``; not by the hook client at runtime.

* :mod:`bonsai_cc.hook.doctor` -- diagnostic checks for the
  ``bonsai-cc doctor`` command.

This ``__init__`` deliberately re-exports **nothing**. Heavy imports
(pydantic, typer, structlog) must not be pulled in just because
someone imports ``bonsai_cc.hook.client_template``; keeping the
package init empty lets the hook client's cold start stay tight.
"""
