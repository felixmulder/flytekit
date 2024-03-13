import typing
from enum import Enum
from typing import Coroutine, Dict, List, Optional, OrderedDict, Tuple, Type, Union

from flytekit.configuration import SerializationSettings
from flytekit.core.base_task import PythonTask, TaskMetadata
from flytekit.core.context_manager import FlyteContext
from flytekit.core.interface import Interface
from flytekit.core.pod_template import PodTemplate
from flytekit.core.promise import Promise, VoidPromise, create_task_output
from flytekit.core.python_auto_container import get_registerable_container_image
from flytekit.core.resources import Resources, ResourceSpec
from flytekit.core.utils import _get_container_definition, _serialize_pod_spec
from flytekit.image_spec.image_spec import ImageSpec
from flytekit.loggers import logger
from flytekit.models import literals as _literal_models
from flytekit.models import task as _task_model
from flytekit.models.security import Secret, SecurityContext

_PRIMARY_CONTAINER_NAME_FIELD = "primary_container_name"
DOCKER_IMPORT_ERROR_MESSAGE = "Docker is not installed. Please install Docker by running `pip install docker`."


class ContainerTask(PythonTask):
    """
    This is an intermediate class that represents Flyte Tasks that run a container at execution time. This is the vast
    majority of tasks - the typical ``@task`` decorated tasks for instance all run a container. An example of
    something that doesn't run a container would be something like the Athena SQL task.
    """

    class MetadataFormat(Enum):
        JSON = _task_model.DataLoadingConfig.LITERALMAP_FORMAT_JSON
        YAML = _task_model.DataLoadingConfig.LITERALMAP_FORMAT_YAML
        PROTO = _task_model.DataLoadingConfig.LITERALMAP_FORMAT_PROTO

    class IOStrategy(Enum):
        DOWNLOAD_EAGER = _task_model.IOStrategy.DOWNLOAD_MODE_EAGER
        DOWNLOAD_STREAM = _task_model.IOStrategy.DOWNLOAD_MODE_STREAM
        DO_NOT_DOWNLOAD = _task_model.IOStrategy.DOWNLOAD_MODE_NO_DOWNLOAD
        UPLOAD_EAGER = _task_model.IOStrategy.UPLOAD_MODE_EAGER
        UPLOAD_ON_EXIT = _task_model.IOStrategy.UPLOAD_MODE_ON_EXIT
        DO_NOT_UPLOAD = _task_model.IOStrategy.UPLOAD_MODE_NO_UPLOAD

    def __init__(
        self,
        name: str,
        image: typing.Union[str, ImageSpec],
        command: List[str],
        inputs: Optional[OrderedDict[str, Type]] = None,
        metadata: Optional[TaskMetadata] = None,
        arguments: Optional[List[str]] = None,
        outputs: Optional[Dict[str, Type]] = None,
        requests: Optional[Resources] = None,
        limits: Optional[Resources] = None,
        input_data_dir: Optional[str] = None,
        output_data_dir: Optional[str] = None,
        metadata_format: MetadataFormat = MetadataFormat.JSON,
        io_strategy: Optional[IOStrategy] = None,
        secret_requests: Optional[List[Secret]] = None,
        pod_template: Optional["PodTemplate"] = None,
        pod_template_name: Optional[str] = None,
        **kwargs,
    ):
        sec_ctx = None
        if secret_requests:
            for s in secret_requests:
                if not isinstance(s, Secret):
                    raise AssertionError(f"Secret {s} should be of type flytekit.Secret, received {type(s)}")
            sec_ctx = SecurityContext(secrets=secret_requests)

        # pod_template_name overwrites the metadata.pod_template_name
        metadata = metadata or TaskMetadata()
        metadata.pod_template_name = pod_template_name

        super().__init__(
            task_type="raw-container",
            name=name,
            interface=Interface(inputs, outputs),
            metadata=metadata,
            task_config=None,
            security_ctx=sec_ctx,
            **kwargs,
        )
        self._image = image
        self._cmd = command
        self._args = arguments
        self._input_data_dir = input_data_dir
        self._output_data_dir = output_data_dir
        self._outputs = outputs
        self._md_format = metadata_format
        self._io_strategy = io_strategy
        self._resources = ResourceSpec(
            requests=requests if requests else Resources(), limits=limits if limits else Resources()
        )
        self.pod_template = pod_template

    @property
    def resources(self) -> ResourceSpec:
        return self._resources

    def local_execute(
        self, ctx: FlyteContext, **kwargs
    ) -> Union[Tuple[Promise], Promise, VoidPromise, Coroutine, None]:
        try:
            import docker
        except ImportError:
            raise ImportError(DOCKER_IMPORT_ERROR_MESSAGE)
        import os

        from flytekit.core.promise import translate_inputs_to_literals
        from flytekit.core.type_engine import TypeEngine, TypeTransformerFailedError

        try:
            kwargs = translate_inputs_to_literals(
                ctx,
                incoming_values=kwargs,
                flyte_interface_types=self.interface.inputs,
                native_types=self.get_input_types(),  # type: ignore
            )
        except TypeTransformerFailedError as exc:
            msg = f"Failed to convert inputs of task '{self.name}':\n  {exc}"
            logger.error(msg)
            raise TypeError(msg) from exc
        input_literal_map = _literal_models.LiteralMap(literals=kwargs)
        try:
            native_inputs = self._literal_map_to_python_input(input_literal_map, ctx)
        except Exception as exc:
            msg = f"Failed to convert inputs of task '{self.name}':\n  {exc}"
            logger.error(msg)
            raise type(exc)(msg) from exc

        container_output_dir = "/flyte/raw-container-task/output"
        output_directory = ctx.file_access.get_random_local_directory()
        volume_bindings = {
            output_directory: {
                "bind": container_output_dir,
                "mode": "rw",
            },
        }

        commands = ""
        if self._cmd:
            for cmd in self._cmd:
                if cmd.startswith("{{.inputs.") and cmd.endswith("}}"):
                    v = cmd[len("{{.inputs.") : -len("}}")]
                    commands += str(native_inputs[v]) + " "
                elif cmd == self._output_data_dir:
                    commands += container_output_dir + " "
                else:
                    commands += cmd + " "
        if self._args:
            for arg in self._args:
                cmd += arg + " "

        client = docker.from_env()
        container = client.containers.run(
            self._image,
            command=[
                "sh",
                "-c",
                commands,
            ],
            volumes=volume_bindings,
            detach=True,
        )
        # Wait for the container to finish the task
        container.wait()
        container.stop()
        container.remove()

        output_dict = {}
        if self._outputs:
            for k, output_type in self._outputs.items():
                with open(os.path.join(output_directory, k), "r") as f:
                    output_val = f.read()
                output_dict[k] = output_type(output_val)
        outputs_literal_map = TypeEngine.dict_to_literal_map(ctx, output_dict)
        outputs_literals = outputs_literal_map.literals
        output_names = list(self.interface.outputs.keys())
        # Tasks that don't return anything still return a VoidPromise
        if len(output_names) == 0:
            return VoidPromise(self.name)
        vals = [Promise(var, outputs_literals[var]) for var in output_names]
        return create_task_output(vals, self.python_interface)

    def get_container(self, settings: SerializationSettings) -> _task_model.Container:
        # if pod_template is specified, return None here but in get_k8s_pod, return pod_template merged with container
        if self.pod_template is not None:
            return None

        return self._get_container(settings)

    def _get_data_loading_config(self) -> _task_model.DataLoadingConfig:
        return _task_model.DataLoadingConfig(
            input_path=self._input_data_dir,
            output_path=self._output_data_dir,
            format=self._md_format.value,
            enabled=True,
            io_strategy=self._io_strategy.value if self._io_strategy else None,
        )

    def _get_image(self, settings: SerializationSettings) -> str:
        if settings.fast_serialization_settings is None or not settings.fast_serialization_settings.enabled:
            if isinstance(self._image, ImageSpec):
                # Set the source root for the image spec if it's non-fast registration
                self._image.source_root = settings.source_root
        return get_registerable_container_image(self._image, settings.image_config)

    def _get_container(self, settings: SerializationSettings) -> _task_model.Container:
        env = settings.env or {}
        env = {**env, **self.environment} if self.environment else env
        return _get_container_definition(
            image=self._get_image(settings),
            command=self._cmd,
            args=self._args,
            data_loading_config=self._get_data_loading_config(),
            environment=env,
            ephemeral_storage_request=self.resources.requests.ephemeral_storage,
            cpu_request=self.resources.requests.cpu,
            gpu_request=self.resources.requests.gpu,
            memory_request=self.resources.requests.mem,
            ephemeral_storage_limit=self.resources.limits.ephemeral_storage,
            cpu_limit=self.resources.limits.cpu,
            gpu_limit=self.resources.limits.gpu,
            memory_limit=self.resources.limits.mem,
        )

    def get_k8s_pod(self, settings: SerializationSettings) -> _task_model.K8sPod:
        if self.pod_template is None:
            return None
        return _task_model.K8sPod(
            pod_spec=_serialize_pod_spec(self.pod_template, self._get_container(settings), settings),
            metadata=_task_model.K8sObjectMetadata(
                labels=self.pod_template.labels,
                annotations=self.pod_template.annotations,
            ),
            data_config=self._get_data_loading_config(),
        )

    def get_config(self, settings: SerializationSettings) -> Optional[Dict[str, str]]:
        if self.pod_template is None:
            return {}
        return {_PRIMARY_CONTAINER_NAME_FIELD: self.pod_template.primary_container_name}
