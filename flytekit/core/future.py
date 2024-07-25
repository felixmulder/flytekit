from __future__ import annotations

import typing
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flytekit.core.base_task import PythonTask
    from flytekit.core.workflow import WorkflowBase


class FlyteFuture:
    def __init__(self, entity: typing.Union[PythonTask, WorkflowBase], version: typing.Optional[str] = None, **kwargs):
        """A Future object that represents a Flyte task or workflow execution.

        This object requires the FlyteRemote client to be initialized before it can be used. The FlyteRemote client
        can be initialized by calling `flytekit.remote.init_remote()`.
        """
        from flytekit.remote.init_remote import REMOTE_ENTRY

        if REMOTE_ENTRY is None:
            raise Exception(
                "Remote Flyte client has not been initialized. Please call flytekit.remote.init_remote() before executing tasks."
            )
        self._remote_entry = REMOTE_ENTRY
        self._exe = self._remote_entry.execute(entity, version=version, inputs=kwargs)

    def wait(
        self,
        timeout: typing.Optional[timedelta] = None,
        poll_interval: typing.Optional[timedelta] = None,
        sync_nodes: bool = True,
    ):
        return self._remote_entry.wait(self._exe, timeout=timeout, poll_interval=poll_interval, sync_nodes=sync_nodes)
