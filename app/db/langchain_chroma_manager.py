import asyncio
import json
import os
from langchain_chroma import Chroma
from langchain_openai import AzureOpenAIEmbeddings
from langchain_core.documents import Document
from langchain_community.graphs import Neo4jGraph

from app.log_manager import get_logger, log_debug, log_info, log_error, log_warn

log = get_logger(__name__)

class LangchainChromaManager:
    def __init__(self):
        """
        Initialize the LangchainChromaManager with default settings.
        """
        self.persist_directory = "app/db/chroma_db_data"
        self.embedding_function = AzureOpenAIEmbeddings(
            deployment=os.getenv("AZURE_ADA_LARGE_DEPLOYMENT_NAME"),
            openai_api_version=os.getenv("AZURE_ADA_LARGE_API_VERSION"),
            azure_endpoint=os.getenv("AZURE_ADA_LARGE_BASE_URL"),
            openai_api_key=os.getenv("AZURE_ADA_LARGE_API_KEY"),
        )

    async def get_or_create_collection(self, collection_name, docs=None, batch_size=30):
        """
        Create or retrieve a Chroma collection. If documents are provided, they will be added in batches of 30.

        Args:
            collection_name (str): The name of the collection.
            docs (list, optional): List of documents to be added to the collection.
            batch_size (int, optional): The size of each batch of documents to be added. Defaults to 30.

        Returns:
            Chroma: The Chroma collection object.
        """
        try:
            log_info(log, f"Getting or creating collection: {collection_name}")
            collection = Chroma(
                persist_directory=self.persist_directory,
                embedding_function=self.embedding_function,
                collection_name=collection_name
            )

            if docs:
                for i in range(0, len(docs), batch_size):
                    batch_docs = docs[i:i + batch_size]
                    collection.add_documents(batch_docs)
                    log_info(log, f"Added batch of documents to collection: {collection_name}")

            return collection
        except Exception as e:
            log_error(log, f"Error in get_or_create_collection: {e}")
            raise

    async def search_documents(self, collection_name, query, k=2):
        """
        Search for documents in the collection that match the query.

        Args:
            collection_name (str): The name of the collection.
            query (str): The search query.
            k (int, optional): The number of top results to return. Defaults to 2.

        Returns:
            list: List of page contents of the matching documents.
        """
        try:
            log_info(log, f"Searching documents in collection: {collection_name} with query: {query}")
            collection = await self.get_or_create_collection(collection_name)
            results = await collection.asimilarity_search(query, k=k)
            return [result.page_content for result in results]
        except Exception as e:
            log_error(log, f"Error in search_documents: {e}")
            raise

    async def degreed_search_knowledge(self, collection_name, query, k=2, related_docs=False):
        """
        Search for documents in the collection that match the query and return raw results.

        Args:
            collection_name (str): The name of the collection.
            query (str): The search query.
            k (int, optional): The number of top results to return. Defaults to 2.

        Returns:
            list: List of matching documents.
        """
        try:
            
            log_info(log, f"Raw searching documents in collection: {collection_name} with query: {query}")
            collection = await self.get_or_create_collection(collection_name)
            results = await collection.asimilarity_search(query, k=k)
            if related_docs:
                # ids = []
                # for result in results:
                #     if related_docs_id in result.metadata:
                #         related_ids = json.loads(result.metadata[related_docs_id])
                #         ids.extend(str(id) for id in related_ids if id is not None)
                # ids = list(set(ids))  
                # related_results_document = []
                # related_results = collection.get(ids)

                # related_results_document = [
                #     Document(page_content=page_content, metadata=metadata)
                #     for page_content, metadata in zip(related_results["documents"], related_results["metadatas"])
                #     if metadata[doc_id] in ids
                # ]
                related_results_document = []
                graph = Neo4jGraph(url=os.getenv("NEO4J_URL"), username=os.getenv("NEO4J_USERNAME"), password=os.getenv("NEO4J_PASSWORD"))
                for result in results:
                    related_article = graph.query("""MATCH (:Article {article_id: $article_id})-[:`Related To`]-(relatedArticle)RETURN relatedArticle""", {"article_id":result.metadata["Article ID"]})
                    for article in related_article[:k]:
                        doc = Document(page_content=article["relatedArticle"]["Body"], metadata={key: value for key, value in article["relatedArticle"].items() if key != "Body"})
                        related_results_document.append(doc)
                            
                return results + related_results_document

            return results
        except Exception as e:
            log_error(log, f"Error in raw_search_documents: {e}")
            raise

    async def delete_collection(self, collection_name):
        """
        Delete the specified collection.

        Args:
            collection_name (str): The name of the collection to delete.

        Returns:
            str: The name of the deleted collection.
        """
        try:
            log_info(log, f"Deleting collection: {collection_name}")
            collection = await self.get_or_create_collection(collection_name)
            collection.delete_collection()
            return collection_name
        except Exception as e:
            log_error(log, f"Error in delete_collection: {e}")
            raise

    async def get_knowledge(self, collection_names, query, k=2):
        """
        Retrieve knowledge from multiple collections based on the query.

        Args:
            collection_names (list): List of collection names.
            query (str): The search query.
            k (int, optional): The number of top results to return from each collection. Defaults to 2.

        Returns:
            list: List of page contents of the matching documents from all collections.
        """
        try:
            log_info(log, f"Retrieving knowledge from collections: {collection_names} with query: {query}")
            knowledge = []

            tasks = [
                self.search_documents(collection_name, query, k)
                for collection_name in collection_names
            ]
            # Run tasks concurrently and gather results
            results = await asyncio.gather(*tasks)

            # Flatten the list of results
            for result in results:
                knowledge.extend(result)

            return knowledge
        except Exception as e:
            log_error(log, f"Error in get_knowledge: {e}")
            raise
