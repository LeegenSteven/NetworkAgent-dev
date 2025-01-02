import logging
logger = logging.getLogger(__name__)


from graph.topology import SPANNER_INSTANCE, SPANNER_DATABASE
from langchain_google_spanner import SecondaryIndex, SpannerVectorStore, TableColumn
from langchain_google_vertexai import VertexAIEmbeddings
from langchain.agents import Tool

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

def retrieve_from_history(history: list):
    """Retrieves documents based on the last message in history."""
    if history:
      last_message = history[-1].content
      return retriever.invoke(last_message)
    return ""

retrieval_tool = Tool(
    name="ResourceDescriptionRetriever",
    func=retrieve_from_history,
    description="Based on the last question, retrieve resource descriptions of network nodes \
      in order of decreasing relevancy. Each description is formatted as a JSON string and \
      contains information about the network node kind, name, status, parent node \
      (also known as OwnerReference), network flow connection (also know as network or subnetwork reference), \
      customer it belongs to. Retrieves documents based on the last message in the conversation history.",
)


