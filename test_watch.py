"""Offline trials that exercise the same parsers, state and delivery code as watch."""
import unittest
from datetime import datetime, timedelta
from tempfile import TemporaryDirectory
from pathlib import Path
from sources import IST, parse_rows
from watcher import database, calendar, record, deliver

DAY = datetime.now(IST).date() + timedelta(days=3)
LINK = "https://in.bookmyshow.com/cinemas/hyderabad/allu-cinemas-kokapet/buytickets/ALUC/"+DAY.strftime("%Y%m%d")
BMS = """
- grid:
  - link "Trial film (U)":
    - /url: /movies/hyderabad/trial/ET123456
  - link "Telugu":
    - /url: /explore/movies?languages=telugu
  - generic: ", 2D"
  - button "Book 01:15 PM":
    - generic: 01:15 PM
    - generic: LASER 4K ATMOS
  - button "Book 01:20 PM" [disabled]:
    - generic: 01:20 PM
    - generic: LASER 4K ATMOS
"""
DISTRICT = """
- link "Trial film U | Telugu":
  - /url: /movies/trial-MV123456
  - img "Trial film"
  - generic: Trial film
  - generic: U | Telugu
- generic: Telugu
- list:
  - button "01:20 PM 2D":
    - link:
      - /url: /movies/seat-layout/fixture
    - text: 01:20 PM
    - generic: 2D
"""

def parse(snapshot,platform="BookMyShow"):
    return parse_rows(snapshot,platform,"ALUC",DAY,LINK)


class WatchTrials(unittest.TestCase):
    def test_delivery_restart_and_dedup(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp)/"db"
            db = database(path)
            messages = []
            calendar(db,"bms",{DAY:LINK})
            records = parse(BMS)
            self.assertEqual([r["open"] for r in records],[True,False])
            record(db,"bms",DAY,records)
            deliver(db,messages.append)
            self.assertEqual(messages,[])
            calendar(db,"district",{})
            record(db,"bms",DAY,parse(BMS.replace(' [disabled]','')))
            calendar(db,"district",{DAY:LINK})
            record(db,"district",DAY,parse(DISTRICT,"District"))
            with self.assertRaises(ConnectionError):
                deliver(db,lambda msg: (_ for _ in ()).throw(ConnectionError()))
            db.close()
            db = database(path)
            deliver(db,messages.append)
            self.assertEqual(len(messages),1)
            self.assertIn("01:20 PM",messages[0])
            self.assertIn("District:",messages[0])
            self.assertIn("BookMyShow:",messages[0])
            record(db,"bms",DAY,parse(BMS.replace(' [disabled]','')))
            deliver(db,messages.append)
            self.assertEqual(len(messages),1)
            db.close()

    def test_no_seat_link_no_district_alert(self):
        self.assertFalse(parse(DISTRICT.replace("/movies/seat-layout/fixture","/movies"),"District")[0]["open"])

    def test_new_date_and_first_district_listing(self):
        for source in ["bms","district"]:
            with self.subTest(source=source):
                db=database(":memory:")
                calendar(db,source,{DAY:None} if source=="bms" else {})
                calendar(db,source,{DAY:LINK})
                rows=parse(BMS) if source=="bms" else parse(DISTRICT,"District")
                record(db,source,DAY,rows)
                sent=[]
                deliver(db,sent.append)
                self.assertEqual(len(sent),1)
                db.close()


if __name__=="__main__":
    unittest.main(verbosity=2)
