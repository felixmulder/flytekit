"""
.. currentmodule:: flytekitplugins.awssagemaker

.. autosummary::
   :template: custom.rst
   :toctree: generated/

   BotoAgent
   BotoTask
   SageMakerModelTask
   SageMakerEndpointConfigTask
   SageMakerEndpointAgent
   SageMakerEndpointTask
   SageMakerDeleteEndpointConfigTask
   SageMakerDeleteEndpointTask
   SageMakerDeleteModelTask
   SageMakerInvokeEndpointTask
   create_sagemaker_deployment
   delete_sagemaker_deployment
"""

from .agent import SageMakerEndpointAgent
from .boto3_agent import BotoAgent
from .boto3_task import BotoConfig, BotoTask
from .task import (
    SageMakerDeleteEndpointConfigTask,
    SageMakerDeleteEndpointTask,
    SageMakerDeleteModelTask,
    SageMakerEndpointConfigTask,
    SageMakerEndpointTask,
    SageMakerInvokeEndpointTask,
    SageMakerModelTask,
)
from .workflow import create_sagemaker_deployment, delete_sagemaker_deployment
