# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
logger = logging.getLogger(__name__)


from graph.topology import SPANNER_INSTANCE, SPANNER_DATABASE
from langchain_google_spanner import SecondaryIndex, SpannerVectorStore, TableColumn
from langchain_google_vertexai import VertexAIEmbeddings
from langchain.agents import Tool

from langchain.tools import BaseTool, StructuredTool, tool
from langchain.callbacks.manager import CallbackManagerForToolRun
from pydantic import BaseModel, Field
from typing import Optional, Type

embeddings = VertexAIEmbeddings(model="text-embedding-005")
resource_vector_store = SpannerVectorStore(
  instance_id=SPANNER_INSTANCE,
  database_id=SPANNER_DATABASE,
  table_name="KgResourceDescriptionNode",
  id_column="id",
  content_column="content",
  embedding_service=embeddings
)

logging_vector_store = SpannerVectorStore(
  instance_id=SPANNER_INSTANCE,
  database_id=SPANNER_DATABASE,
  table_name="KgLogEntryNode",
  id_column="id",
  content_column="content",
  metadata_columns=["timestamp", "severity", "message"],
  embedding_service=embeddings
)

resource_retriever = resource_vector_store.as_retriever(search_kwargs={"k": 8})
logging_retriever = logging_vector_store.as_retriever(search_kwargs={"k": 20})

class ResourceRetrievalInput(BaseModel):
  messages: list = Field(description="message history")
  question: str = Field(description="last question asked")

class ResourceRetrievalTool(BaseTool):
  name: str = "ResourceDescriptionRetriever"
  description: str = "Based on the last question, retrieve resource descriptions of network nodes \
    in order of decreasing relevancy. Each description is formatted as a JSON string and \
    contains information about the network node kind, name, status, parent node \
    (also known as OwnerReference), network flow connection (also know as network or subnetwork reference)."
  args_schema: Type[BaseModel] = ResourceRetrievalInput

  def _run(self, messages: list, question: str, 
           run_manager: Optional[CallbackManagerForToolRun] = None) -> str:
      logger.debug(f"Resource Retriever called with query: {question}")
      return resource_retriever.invoke(question)

class LoggingRetrievalInput(BaseModel):
  messages: list = Field(description="message history")
  question: str = Field(description="last question asked")

class LoggingRetrievalTool(BaseTool):
  name: str = "LoggingRetriever"
  description: str = "Based on the last question, retrieve relevant log entries \
    in order of decreasing relevancy. Each description is formatted as a text line and \
    contains a timestamp, followed by the severity of the log and then the log message. \
    The log message may contain references to network nodes id, kind and name."
  args_schema: Type[BaseModel] = LoggingRetrievalInput

  def _run(self, messages: list, question: str, 
           run_manager: Optional[CallbackManagerForToolRun] = None) -> str:
      logger.debug(f"Logging Retriever called with query: {question}")
      return logging_retriever.invoke(question)


