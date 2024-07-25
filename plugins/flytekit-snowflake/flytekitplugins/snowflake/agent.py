from dataclasses import dataclass
from typing import Optional

from flyteidl.core.execution_pb2 import TaskExecution

import flytekit
from flytekit import FlyteContextManager, StructuredDataset, logger
from flytekit.core.type_engine import TypeEngine
from flytekit.extend.backend.base_agent import AgentRegistry, AsyncAgentBase, Resource, ResourceMeta
from flytekit.extend.backend.utils import convert_to_flyte_phase
from flytekit.models import literals
from flytekit.models.literals import LiteralMap
from flytekit.models.task import TaskTemplate
from flytekit.models.types import LiteralType, StructuredDatasetType
from snowflake import connector as sc

TASK_TYPE = "snowflake"
SNOWFLAKE_PRIVATE_KEY = "snowflake_private_key"


@dataclass
class SnowflakeJobMetadata(ResourceMeta):
    user: str
    account: str
    database: str
    schema: str
    warehouse: str
    table: str
    query_id: str
    output: bool


def get_private_key():
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization

    pk_string = flytekit.current_context().secrets.get(SNOWFLAKE_PRIVATE_KEY, encode_mode="r")
    # cryptography needs str to be stripped and converted to bytes
    pk_string = pk_string.strip().encode()
    p_key = serialization.load_pem_private_key(pk_string, password=None, backend=default_backend())

    pkb = p_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    return pkb


def get_connection(metadata: SnowflakeJobMetadata) -> sc:
    return sc.connect(
        user=metadata.user,
        account=metadata.account,
        private_key=get_private_key(),
        database=metadata.database,
        schema=metadata.schema,
        warehouse=metadata.warehouse,
    )


class SnowflakeAgent(AsyncAgentBase):
    name = "Snowflake Agent"

    def __init__(self):
        super().__init__(task_type_name=TASK_TYPE, metadata_type=SnowflakeJobMetadata)

    async def create(
        self, task_template: TaskTemplate, inputs: Optional[LiteralMap] = None, **kwargs
    ) -> SnowflakeJobMetadata:
        ctx = FlyteContextManager.current_context()
        literal_types = task_template.interface.inputs

        params = TypeEngine.literal_map_to_kwargs(ctx, inputs, literal_types=literal_types) if inputs.literals else None

        config = task_template.config
        conn = sc.connect(
            user=config["user"],
            account=config["account"],
            private_key=get_private_key(),
            database=config["database"],
            schema=config["schema"],
            warehouse=config["warehouse"],
        )

        cs = conn.cursor()
        cs.execute_async(task_template.sql.statement, params)

        return SnowflakeJobMetadata(
            user=config["user"],
            account=config["account"],
            database=config["database"],
            schema=config["schema"],
            warehouse=config["warehouse"],
            table=config["table"],
            query_id=cs.sfqid,
            output=task_template.interface.outputs and len(task_template.interface.outputs) > 0,
        )

    async def get(self, resource_meta: SnowflakeJobMetadata, **kwargs) -> Resource:
        conn = get_connection(resource_meta)
        try:
            query_status = conn.get_query_status_throw_if_error(resource_meta.query_id)
        except sc.ProgrammingError as err:
            logger.error("Failed to get snowflake job status with error:", err.msg)
            return Resource(phase=TaskExecution.FAILED)

        # The snowflake job's state is determined by query status.
        # https://github.com/snowflakedb/snowflake-connector-python/blob/main/src/snowflake/connector/constants.py#L373
        cur_phase = convert_to_flyte_phase(str(query_status.name))
        res = None

        if cur_phase == TaskExecution.SUCCEEDED and resource_meta.output:
            ctx = FlyteContextManager.current_context()
            uri = f"snowflake://{resource_meta.user}:{resource_meta.account}/{resource_meta.warehouse}/{resource_meta.database}/{resource_meta.schema}/{resource_meta.query_id}"
            res = literals.LiteralMap(
                {
                    "results": TypeEngine.to_literal(
                        ctx,
                        StructuredDataset(uri=uri),
                        StructuredDataset,
                        LiteralType(structured_dataset_type=StructuredDatasetType(format="")),
                    )
                }
            )
        return Resource(phase=cur_phase, outputs=res)

    async def delete(self, resource_meta: SnowflakeJobMetadata, **kwargs):
        conn = get_connection(resource_meta)
        cs = conn.cursor()
        try:
            cs.execute(f"SELECT SYSTEM$CANCEL_QUERY('{resource_meta.query_id}')")
            cs.fetchall()
        finally:
            cs.close()
            conn.close()


AgentRegistry.register(SnowflakeAgent())
