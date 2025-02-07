from typing import List
import uuid
import os

from pydantic import BaseModel
from app.db.chroma_client import CHROMA_CLIENT
import chromadb.utils.embedding_functions as embedding_functions


class Documents(BaseModel):
    documents: List[str]
    ids: List[str] = []
    embeddings: List[List[float]] = []

    def generate_ids_and_embeddings(self, embedding_function):
        # Generate UUIDs for each document
        self.ids = [str(uuid.uuid4()) for _ in self.documents]
        
        # Generate embeddings for each document using the provided embedding function
        self.embeddings = embedding_function(input=self.documents)


class ChromaManager:
    def __init__(self):
        self.chroma_client = CHROMA_CLIENT
        self.initiate_embedding_function()

    def initiate_embedding_function(self):
        # For OpenAI Implementation
        # self.embedding_function = embedding_functions.OpenAIEmbeddingFunction(
        #                 api_key="YOUR_API_KEY",
        #                 model_name="text-embedding-3-small"
        #             )

        # For Azure Implementation
        self.embedding_function = embedding_functions.OpenAIEmbeddingFunction(
            api_key=os.getenv("AZURE_ADA_LARGE_API_KEY"),
            api_base=os.getenv("AZURE_ADA_LARGE_BASE_URL"),
            api_type="azure",
            api_version=os.getenv("AZURE_ADA_LARGE_API_VERSION"),
            model_name=os.getenv("AZURE_ADA_LARGE_DEPLOYMENT_NAME")
        )

    def get_or_create_collection(self, name):
        collection = self.chroma_client.get_or_create_collection(name=name)
        return collection

    def add_document(self, collection, document):
        collection.upsert(document)
        return document

    def create_and_add_documents(self, collection_name, document_texts):
        # Create a new Documents instance
        docs = Documents(documents=document_texts)
        
        # Generate UUIDs and embeddings for the documents
        docs.generate_ids_and_embeddings(self.embedding_function)
        
        # Get or create the collection
        collection = self.get_or_create_collection(collection_name)
        
        # Prepare documents to be added to the collection
        documents_to_add = [
            {"id": doc_id, "document": doc, "embedding": embedding}
            for doc_id, doc, embedding in zip(docs.ids, docs.documents, docs.embeddings)
        ]
        
        # Add documents to the collection
        for doc in documents_to_add:
            self.add_document(collection, doc)
        
        return docs
    
    def search_documents(self, collection_name, query, num_results=10):
        collection = self.get_or_create_collection(collection_name)
        search_results = collection.query(query_texts=[query], num_results=num_results)
        return search_results
