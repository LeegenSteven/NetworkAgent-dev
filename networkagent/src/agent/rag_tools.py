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
vector_store = SpannerVectorStore(
  instance_id=SPANNER_INSTANCE,
  database_id=SPANNER_DATABASE,
  table_name="KgResourceDescriptionNode",
  id_column="id",
  content_column="content",
  embedding_service=embeddings
)

retriever = vector_store.as_retriever(search_kwargs={"k": 8})

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
      return retriever.invoke(question)




