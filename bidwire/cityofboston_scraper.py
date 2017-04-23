from bid import Bid, get_new_identifiers
from db import Session
from datetime import datetime
from lxml import etree, html
from base_scraper import BaseScraper
import logging
import scrapelib
import concurrent.futures
import re

# Logger object for this module
log = logging.getLogger(__name__)

compiled_reg_exp = re.compile("bids\.asp\?ID=(\d+)")

# Number of concurrent threads to process results page
NUMBER_OF_THREADS = 5


class CityOfBostonScraper(BaseScraper):
    def __init__(self):
        self.results_url = "https://www.cityofboston.gov/purchasing/bid.asp"
        self.details_url = "https://www.cityofboston.gov/purchasing/bids.asp"

    def scrape(self):
        """Iterates through a single results page and extracts bids.

        This is implemented as follows:
          1. Download the results page.
          2. Extract the bid identifiers from this page.
          3. Check which of those identifiers are not yet in our database.
          4. For each of the identifiers not yet in our database:
            4.1. Download the detail page for each identifier.
            4.2. Extract the fields we are interested in.
            4.3. Create a Bid object and store it in the database.
        """
        scraper = scrapelib.Scraper()
        session = Session()
        page = scraper.get(self.results_url)
        bid_ids = self.scrape_results_page(page.content)
        log.info("Found bid ids: {}".format(bid_ids))
        new_ids = get_new_identifiers(session, bid_ids, self.get_site())
        self.process_new_bids(new_ids, session, scraper)
        # Save all the new bids from this results page in one db call.
        session.commit()

    def process_new_bids(self, new_ids, session, scraper):
        """Gets bid details from results page and adds Bid objects to db session

        Args:
        new_ids -- list of new bid ids
        session -- the active database session
        scraper -- scraper object
        """
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=NUMBER_OF_THREADS
        ) as executor:
            # Use a thread pool for concurrently retrieving the HTML data
            futures = list(map(lambda bid_id:
                               executor.submit(
                                   self.get_details_for_bid, scraper,
                                   bid_id), new_ids))
            for future in concurrent.futures.as_completed(futures):
                try:
                    bid_page, bid_id = future.result()
                except Exception as exc:
                    log.error("Exception: {}".format(exc))
                else:
                    bid = self.scrape_bid_page(bid_page, bid_id)
                    log.info("Found new bid: {}".format(bid))
                    session.add(bid)

    def get_site(self):
        return Bid.Site.CITYOFBOSTON

    def get_details_for_bid(self, scraper, bid_id):
        """Gets bid details from results page"""
        return scraper.get(self.details_url, params={'ID': bid_id}), bid_id

    def scrape_results_page(self, page_str):
        """Scrapes the City of Boston results page.

        Args:
        page_str -- the entire HTML page as a string

        Returns:
        bid_ids -- a list of strings with the bid identifiers found
        """
        tree = html.fromstring(page_str)
        # Bid urls are encoded as `bids.asp?ID=<bidId>` in the table
        bid_id_urls = tree.xpath('//b/a/@href')
        bid_ids = []
        for bid_id_url in bid_id_urls:
            bid_id = self.get_bid_id(bid_id_url)
            if bid_id is None:
                continue
            bid_ids.append("".join(bid_id).strip())
        return bid_ids

    def get_bid_id(self, href):
        """Extracts the ID from the href link"""
        regexp_match = compiled_reg_exp.match(href)
        if regexp_match:
            return regexp_match.group(1)
        return None

    def scrape_bid_page(self, page, bid_id):
        """Scrapes the given page as a City of Boston bid detail page.

        Relies on the position of information inside the main results table,
        since the HTML contains no semantically-meaninful ids or classes.

        Raises ValueError if it encounters parsing errors.
        """
        tree = html.fromstring(page.content)
        first_center = tree.xpath('//center')[0]
        start_text_element = first_center.xpath('b')[0]
        description = start_text_element.text.strip()
        items = ["".join(first_center.xpath('text()'))]
        return Bid(
            identifier=bid_id,
            description=description,
            items=items,
            site=self.get_site().name
        )
