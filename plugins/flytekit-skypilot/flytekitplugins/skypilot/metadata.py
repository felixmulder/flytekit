from dataclasses import dataclass
from flytekit.extend.backend.base_agent import ResourceMeta
from flytekitplugins.skypilot.task import JobLaunchType


@dataclass
class SkyPilotMetadata(ResourceMeta):
    """
    This is the metadata for the job.
    """
    job_name: str
    cluster_name: str
    task_metadata_prefix: str
    tracker_hostname: str
    job_launch_type: JobLaunchType