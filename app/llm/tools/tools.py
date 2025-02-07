
import asyncio
import concurrent.futures
import json
import os
import re
from fastapi import HTTPException
import traceback
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from app.db.redis_manager import RedisManager

from app.llm.llm_client import AZURE_ASYNC_CLIENT
from app.llm.prompt import KEYWORD_SUMMARY_TEMPLATE, KEYWORD_SUMMARY_TEMPLATE_JSON, MORNING_SUMMARY_TEMPLATE, MORNING_SUMMARY_TEMPLATE_JSON
from app.llm.prompt_preprocessor import PromptPreprocessor
from app.log_manager import get_logger, log_debug, log_info, log_error, log_warn
from app.utils.llm_utils import num_tokens_from_messages, num_tokens_from_string
from app.dg_component.coach.coach import get_coach

log = get_logger(__name__)

class DuckDuckGoNewsFetcher:
    """Class to fetch news articles and their text content using DuckDuckGo."""

    def __init__(self, region: str = "wt-wt", safesearch: str = "moderate", time: str = "d"):
        """
        Initialize the DuckDuckGoNewsFetcher with default parameters.

        Args:
            region (str): Region for the search. Defaults to "in-en".
            safesearch (str): Safesearch setting. Defaults to "moderate".
            time (str): Time limit for the search. Defaults to "d".
        """
        self.region = region
        self.safesearch = safesearch
        self.time = time

    def convert_timezone_to_region(self, time_zone: str) -> str:
        """
        Convert time zone to region for DuckDuckGo search.

        Args:
            time_zone (str): Time zone.

        Returns:
            str: Region for the time zone.
        """
        # Mapping of time zones to regions
        time_zone_region_map = {
            "America/New_York": "us-en",
            "America/Los_Angeles": "us-en",
            "Asia/Kolkata": "in-en",
            "Europe/London": "uk-en",
        }
        return time_zone_region_map.get(time_zone, "wt-wt")

    def ddgs_news(self, query: str, max_results: int = 10) -> List[Dict[str, str]]:
        """
        Run query through DuckDuckGo news search and return results.

        Args:
            query (str): Search query.
            max_results (Optional[int]): Maximum number of results to fetch. Defaults to None.

        Returns:
            List[Dict[str, str]]: List of news articles.
        """
        from duckduckgo_search import DDGS

        try:
            with DDGS() as ddgs:
                ddgs_gen = ddgs.news(
                    query,
                    region=self.region,
                    safesearch=self.safesearch,
                    timelimit=self.time,
                    max_results=max_results,
                )
                if ddgs_gen:
                    return [r for r in ddgs_gen]
        except Exception as e:
            log_error(log, f"Error fetching news from DuckDuckGo: {e}")
            log_debug(log, traceback.format_exc())
            raise
        return []

    @staticmethod
    def fetch_article_text(url: str) -> str:
        """
        Fetch the text content from a given URL.

        Args:
            url (str): URL of the article.

        Returns:
            str: Text content of the article.
        """
        try:
            response = requests.get(url)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            return soup.get_text(separator=' ', strip=True)
        except requests.RequestException as e:
            log_error(log, f"Error fetching {url}: {e}")
            log_debug(log, traceback.format_exc())
            raise

    def add_text_to_articles(self, articles: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """
        Fetch text for each article URL and add it to the article dictionary.

        Args:
            articles (List[Dict[str, str]]): List of articles.

        Returns:
            List[Dict[str, str]]: List of articles with text content added.
        """
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = {executor.submit(self.fetch_article_text, article['url']): article for article in articles}
            for future in concurrent.futures.as_completed(futures):
                article = futures[future]
                try:
                    article['text'] = future.result()
                except Exception as e:
                    log_error(log, f"Error processing article {article['url']}: {e}")
                    log_debug(log, traceback.format_exc())
                    article['text'] = ""
        return articles
    
    def filter_recent_articles(self, articles: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """
        Filter articles to include only today's and yesterday's news.

        Args:
            articles (List[Dict[str, str]]): List of articles.

        Returns:
            List[Dict[str, str]]: Filtered list of articles.
        """
        today = datetime.utcnow().date()
        yesterday = today - timedelta(days=1)

        def is_recent(article_date: str) -> bool:
            article_date = datetime.fromisoformat(article_date.replace('Z', '+00:00')).date()
            return article_date == today or article_date == yesterday

        return [article for article in articles if is_recent(article['date'])]

    def fetch_news(self, query: str, max_results: int = 10) -> List[Dict[str, str]]:
        """
        Fetch news articles for a given query.

        Args:
            query (str): Search query.
            max_results (Optional[int]): Maximum number of results to fetch. Defaults to 10.

        Returns:
            List[Dict[str, str]]: List of news articles.
        """
        try:
            news_articles = self.ddgs_news(query, max_results)
            articles_with_text = self.add_text_to_articles(news_articles)
            articles_with_text = [article for article in articles_with_text if len(article.get('text')) > 50]
            articles_with_text = self.filter_recent_articles(articles_with_text)
            return articles_with_text
        except Exception as e:
            log_error(log, f"Error fetching news: {e}")
            log_debug(log, traceback.format_exc())
            raise


class MorningSummary:
    def __init__(self):
        self.fetcher = DuckDuckGoNewsFetcher()
        self.redis_manager = RedisManager()
        self.prompt_preprocessor = PromptPreprocessor()
        self.llm_client = AZURE_ASYNC_CLIENT

    def format_articles(self, articles):
        """
        Format a list of articles into a string.

        Args:
            articles (List[Dict[str, str]]): List of articles.

        Returns:
            str: Formatted string of articles.
        """
        formatted_str = ""
        for article in articles:
            title = article.get('title', 'No Title')
            source = article.get('source', 'No Source')
            text = article.get('text', 'No Text')
            url = article.get('url', 'No URL')
            
            formatted_str += f"Title: {title} ({source})\n"
            formatted_str += f"{text}\n"
            formatted_str += f"URL: {url}\n\n"
        
        return formatted_str
    
    def format_keyword_summary(self, keyword_summary):
        """
        Format a keyword summary into a string.

        Args:
            keyword_summary (Dict[str, Dict]): A dictionary where each keyword maps to its corresponding summary and additional information.

        Returns:
            str: Formatted string of keyword summaries.
        """
        formatted_str = ""
        for keyword, summary in keyword_summary.items():
            formatted_str += f"Keyword: {keyword}\n"
            formatted_str += f"{summary['summary']}\n"
            formatted_str += "URLs:\n"
            for url in summary['urls']:
                formatted_str += f"{url}\n"
            formatted_str += "Sources:\n"
            for source in summary['sources']:
                formatted_str += f"{source}\n"
            formatted_str += "\n"
        
        return formatted_str

    async def prepare_summary(self, query: str, max_results: int = 10):
        """
        Prepare a summary for a given query.

        Args:
            query (str): Search query.
            max_results (int): Maximum number of results to fetch. Defaults to 10.

        Returns:
            Dict[str, Any]: Parsed content containing keyword summary, URLs, and sources.
        """
        try:
            news_articles = self.fetcher.fetch_news(query, max_results)
            formatted_news_articles = self.format_articles(news_articles)

            messages = [
                {
                    "role": "system",
                    "content": KEYWORD_SUMMARY_TEMPLATE.format(content=formatted_news_articles, morning_summary_json=KEYWORD_SUMMARY_TEMPLATE_JSON)
                }
            ]
            log_info(log, f"Prompt token of prepare_summary: {num_tokens_from_messages(messages)}")
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    response = await self.llm_client.chat.completions.create(
                        model=os.getenv("AZURE_GPT_4O_DEPLOYMENT_NAME"),
                        messages=messages,
                        temperature=1
                    )
                    
                    content = response.choices[0].message.content
                    log_info(log, f"Response token of prepare_summary: {num_tokens_from_string(content)}")
                    parsed_content = {}

                    # matches = re.findall(r"```json(.*?)```", content, re.DOTALL)
                    # parsed_content = json.loads(matches[0])
                    parsed_content["keyword_summary"] = content
                    parsed_content["url"] = [article["url"] for article in news_articles]
                    parsed_content["source"] = list(set([article["source"] for article in news_articles]))
                    break  # Exit the loop if successful
                except Exception as e:
                    log_error(log, f"Attempt {attempt + 1} failed: {e}")
                    log_debug(log, traceback.format_exc())
                    if attempt == max_retries - 1:
                        raise

            return parsed_content
        except Exception as e:
            log_error(log, f"Error preparing summary: {e}")
            log_debug(log, traceback.format_exc())
            raise

    async def ultimate_summary(self, kewword_summary: dict, user_id: str, coach_id: str) -> Dict[str, Dict]:
        """
        Generate morning summaries for a list of keywords.

        Args:
            keywords (List[str]): List of keywords to search for news articles.
            max_results (int): Maximum number of results to fetch for each keyword. Defaults to 20.

        Returns:
            Dict[str, Dict]: A dictionary where each keyword maps to its corresponding summary and additional information.
        """
        try:
            formatted_keyword_summaries = self.format_keyword_summary(kewword_summary)
            with open(COACH_DATA_PATH, 'r') as file:  # TODO:: Get Coach data from .Net Layer but as of now MorningSummary is not being user
                coach_data = json.load(file)

            # coach_data = await get_coach(self.call_id, self.coach_id)

            user_data = self.redis_manager.retrieve_chat(user_id)
            user_preferences = user_data.get(coach_id, {}).get("user_profile_preferences", "No Preferences")
            if isinstance(user_preferences, dict):
                structured_user_preferences = self.prompt_preprocessor.format_user_preferences(user_preferences)
            else:
                structured_user_preferences = user_preferences

            messages = [
                    {
                        "role": "system",
                        "content": MORNING_SUMMARY_TEMPLATE.format(coach_name=coach_data.get(coach_id, {}).get("coach_name", "No Name"),content=formatted_keyword_summaries, coach_instruction=coach_data.get(coach_id, {}).get("instructions", "No Instructions"),user_preferences=structured_user_preferences,morning_summary_json=MORNING_SUMMARY_TEMPLATE_JSON)
                    }
                ]
            log_info(log, f"Prompt token of ultimate_summary: {num_tokens_from_messages(messages)}")

            response = await self.llm_client.chat.completions.create(
                model=os.getenv("AZURE_GPT_4O_DEPLOYMENT_NAME"),
                messages=messages,
                temperature=1
            )
            
            content = response.choices[0].message.content
            log_info(log, f"Response token of ultimate_summary: {num_tokens_from_string(content)}")
            parsed_content = {}
            # matches = re.findall(r"```json(.*?)```", content, re.DOTALL)
            # parsed_content = json.loads(matches[0])

            parsed_content["morning_summary"] = content
            parsed_content["url"] = [url for keyword, summary in kewword_summary.items() for url in summary["urls"]]
            parsed_content["source"] = list(set(source for keyword, summary in kewword_summary.items() for source in summary["sources"]))

            return parsed_content
        except Exception as e:
            log_error(log, f"Error generating ultimate summary: {e}")
            log_debug(log, traceback.format_exc())
            raise

    async def morning_summary(self, user_id: str, coach_id: str, max_results: int = 20) -> Dict[str, Dict]:
        """
        Generate morning summaries for a list of keywords.

        Args:
            user_id (str): ID of the user.
            coach_id (str): ID of the coach.
            max_results (int): Maximum number of results to fetch for each keyword. Defaults to 20.

        Returns:
            Dict[str, Dict]: A dictionary where each keyword maps to its corresponding summary and additional information.
        """
        try:
            with open(COACH_DATA_PATH, 'r') as file: # TODO:: Get Coach data from .Net Layer but as of now MorningSummary is not being used
                coach_data = json.load(file)
                
            # coach_data = await get_coach(self.call_id, self.coach_id)

            coach_keywords = coach_data.get("keywords", [])
            user_keywords = self.redis_manager.retrieve_chat(user_id).get(coach_id, {}).get("morning_summary_keywords", [])

            keywords = list(set(coach_keywords + user_keywords))

            if not keywords:
                raise HTTPException(status_code=400, detail="No keywords found for the coach.")
            
            tasks = {
                keyword: self.prepare_summary(keyword, max_results)
                for keyword in keywords
            }
            responses = await asyncio.gather(*tasks.values(), return_exceptions=True)

            response_dict = {}
            for keyword, response in zip(tasks.keys(), responses):
                if isinstance(response, Exception):
                    log_error(log, f"Error occurred while processing keyword '{keyword}': {response}")
                    log_debug(log, traceback.format_exc())
                    response_dict[keyword] = {"keyword_summary": "No summary available.", "url": [], "source": []}
                else:
                    response_dict[keyword] = response

            # Process the responses to include additional information
            processed_responses = {}
            for keyword, response in response_dict.items():
                processed_responses[keyword] = {
                    "summary": response.get("keyword_summary", "No summary available."),
                    "urls": response.get("url", []),
                    "sources": response.get("source", [])
                }

            morning_summary = await self.ultimate_summary(processed_responses, user_id, coach_id)

            return morning_summary
        except Exception as e:
            log_error(log, f"Error generating morning summary: {e}")
            log_debug(log, traceback.format_exc())
            print(f"Error generating morning summary: {e}")
            raise HTTPException(status_code=500, detail="Error generating morning summary")
