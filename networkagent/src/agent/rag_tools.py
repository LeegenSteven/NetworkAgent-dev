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
    (also known as OwnerReference), network flow connection (also know as network or subnetwork reference), \
    customer it belongs to."
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


