# Copyright 2026 Google LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Test cases for the campaigns tools."""

import json
import unittest
from unittest.mock import MagicMock, patch

from fastmcp.exceptions import ToolError
from google.ads.googleads.client import GoogleAdsClient
from google.auth.credentials import AnonymousCredentials

from ads_mcp.tools import campaigns


def _fn(tool):
    return getattr(tool, "fn", tool)


class TestCampaigns(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.real_ga_client = GoogleAdsClient(
            credentials=AnonymousCredentials(),
            developer_token="fake-token",
            use_proto_plus=True,
        )

    def setUp(self):
        self.client = MagicMock()
        self.client.get_type.side_effect = self.real_ga_client.get_type
        self.client.enums = self.real_ga_client.enums

        row = MagicMock()
        row.campaign.name = "Mi campana"
        row.campaign.status.name = "ENABLED"
        row.campaign.campaign_budget = "customers/123/campaignBudgets/9"
        row.campaign_budget.amount_micros = 10_000_000

        self.search_service = MagicMock()
        self.search_service.search.return_value = [row]
        self.campaign_service = MagicMock()
        self.budget_service = MagicMock()
        resp = MagicMock()
        resp.results = [MagicMock(resource_name="customers/123/campaigns/55")]
        self.campaign_service.mutate_campaigns.return_value = resp
        bresp = MagicMock()
        bresp.results = [
            MagicMock(resource_name="customers/123/campaignBudgets/9")
        ]
        self.budget_service.mutate_campaign_budgets.return_value = bresp

        services = {
            "GoogleAdsService": self.search_service,
            "CampaignService": self.campaign_service,
            "CampaignBudgetService": self.budget_service,
        }

        p1 = patch(
            "ads_mcp.utils.get_googleads_client", return_value=self.client
        )
        p2 = patch(
            "ads_mcp.utils.get_googleads_service_from_client",
            side_effect=lambda c, name: services[name],
        )
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def _status(self, **kw):
        return _fn(campaigns.update_campaign_status)(
            customer_id="123-456", campaign_id="55", status="PAUSED", **kw
        )

    def test_status_proposal_only_validates(self):
        out = json.loads(self._status())
        self.assertEqual(out["status"], "PROPOSED")
        item = out["change_items"][0]
        self.assertEqual(item["value_readable"], "PAUSED")
        self.assertEqual(item["original_value_readable"], "ENABLED")
        req = self.campaign_service.mutate_campaigns.call_args.kwargs["request"]
        self.assertTrue(req.validate_only)

    def test_status_commit_requires_checksum(self):
        with self.assertRaises(ToolError):
            self._status(validate_only=False)

    def test_status_commit_with_checksum(self):
        checksum = json.loads(self._status())["checksum"]
        out = self._status(validate_only=False, checksum=checksum)
        self.assertIn("Successfully set campaign", out)
        req = self.campaign_service.mutate_campaigns.call_args.kwargs["request"]
        self.assertFalse(req.validate_only)

    def test_status_wrong_checksum_rejected(self):
        with self.assertRaises(ToolError):
            self._status(validate_only=False, checksum="abc")

    def test_status_rejects_invalid_status(self):
        with self.assertRaises(ToolError):
            _fn(campaigns.update_campaign_status)(
                customer_id="123", campaign_id="55", status="REMOVED"
            )

    def test_budget_proposal_and_commit(self):
        fn = _fn(campaigns.update_campaign_budget)
        out = json.loads(
            fn(customer_id="123", campaign_id="55", daily_budget=25.5)
        )
        item = out["change_items"][0]
        self.assertEqual(item["value_readable"], "25.50")
        self.assertEqual(item["original_value_readable"], "10.00")
        res = fn(
            customer_id="123",
            campaign_id="55",
            daily_budget=25.5,
            validate_only=False,
            checksum=out["checksum"],
        )
        self.assertIn("25.50", res)
        req = self.budget_service.mutate_campaign_budgets.call_args.kwargs[
            "request"
        ]
        self.assertEqual(req.operations[0].update.amount_micros, 25_500_000)

    def test_budget_checksum_bound_to_amount(self):
        fn = _fn(campaigns.update_campaign_budget)
        checksum = json.loads(
            fn(customer_id="123", campaign_id="55", daily_budget=25.5)
        )["checksum"]
        with self.assertRaises(ToolError):
            fn(
                customer_id="123",
                campaign_id="55",
                daily_budget=999,
                validate_only=False,
                checksum=checksum,
            )

    def test_budget_rejects_non_positive(self):
        with self.assertRaises(ToolError):
            _fn(campaigns.update_campaign_budget)(
                customer_id="123", campaign_id="55", daily_budget=0
            )

    def test_campaign_not_found(self):
        self.search_service.search.return_value = []
        with self.assertRaises(ToolError):
            self._status()


if __name__ == "__main__":
    unittest.main()


class TestCreateSearchCampaign(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.real = GoogleAdsClient(
            credentials=AnonymousCredentials(),
            developer_token="fake-token",
            use_proto_plus=True,
        )

    def setUp(self):
        self.client = MagicMock()
        self.client.get_type.side_effect = self.real.get_type
        self.client.enums = self.real.enums
        self.ga_service = MagicMock()
        r = MagicMock()
        r.campaign_result.resource_name = "customers/123/campaigns/777"
        resp = MagicMock()
        resp.mutate_operation_responses = [r]
        self.ga_service.mutate.return_value = resp
        p1 = patch("ads_mcp.utils.get_googleads_client", return_value=self.client)
        p2 = patch(
            "ads_mcp.utils.get_googleads_service_from_client",
            return_value=self.ga_service,
        )
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def _ag(self, **over):
        ag = {
            "name": "Goteras",
            "keywords": ['"goteras en el techo cali"', "[reparar gotera]", "techo"],
            "headlines": ["Reparamos Goteras", "Visita Gratis", "Techos Cali"],
            "descriptions": ["Cotiza gratis por WhatsApp.", "Garantia en reparaciones."],
            "final_url": "https://example.com",
        }
        ag.update(over)
        return ag

    def _call(self, **over):
        kw = dict(
            customer_id="123-456",
            name="Test",
            daily_budget=20000,
            geo_target_ids=[1, 2],
            ad_groups=[self._ag()],
            start_date="2026-11-23",
            end_date="2026-12-06",
            negative_keywords=["gratis"],
        )
        kw.update(over)
        return _fn(campaigns.create_search_campaign)(**kw)

    def test_proposal_validates_and_builds_paused_campaign(self):
        out = json.loads(self._call(max_cpc=2000))
        self.assertEqual(out["status"], "PROPOSED")
        fields = {i["field_readable"]: i["value_readable"] for i in out["change_items"]}
        self.assertEqual(fields["Status"], "PAUSED")
        self.assertEqual(fields["Keywords"], "3")
        req = self.ga_service.mutate.call_args.kwargs["request"]
        self.assertTrue(req.validate_only)
        camp = [
            o.campaign_operation.create
            for o in req.mutate_operations
            if o.campaign_operation.create.name
        ][0]
        self.assertEqual(camp.status.name, "PAUSED")
        self.assertEqual(camp.target_spend.cpc_bid_ceiling_micros, 2_000_000_000)
        self.assertFalse(camp.network_settings.target_search_network)

    def test_default_schedule_is_mon_fri_8_18(self):
        self._call()
        req = self.ga_service.mutate.call_args.kwargs["request"]
        sch = [
            o.campaign_criterion_operation.create.ad_schedule
            for o in req.mutate_operations
            if o.campaign_criterion_operation.create.ad_schedule.end_hour
        ]
        self.assertEqual(len(sch), 5)
        self.assertTrue(all(s.start_hour == 8 and s.end_hour == 18 for s in sch))

    def test_keyword_match_types(self):
        self._call()
        req = self.ga_service.mutate.call_args.kwargs["request"]
        kws = [
            (k.keyword.text, k.keyword.match_type.name)
            for k in (
                o.ad_group_criterion_operation.create
                for o in req.mutate_operations
            )
            if k.keyword.text
        ]
        self.assertIn(("goteras en el techo cali", "PHRASE"), kws)
        self.assertIn(("reparar gotera", "EXACT"), kws)
        self.assertIn(("techo", "BROAD"), kws)

    def test_commit_requires_checksum_and_creates(self):
        with self.assertRaises(ToolError):
            self._call(validate_only=False)
        checksum = json.loads(self._call())["checksum"]
        out = self._call(validate_only=False, checksum=checksum)
        self.assertIn("777", out)
        req = self.ga_service.mutate.call_args.kwargs["request"]
        self.assertFalse(req.validate_only)

    def test_checksum_bound_to_params(self):
        checksum = json.loads(self._call())["checksum"]
        with self.assertRaises(ToolError):
            self._call(daily_budget=99999, validate_only=False, checksum=checksum)

    def test_rejects_bad_inputs(self):
        bad = [
            dict(daily_budget=0),
            dict(geo_target_ids=[]),
            dict(ad_groups=[]),
            dict(start_date="23/11/2026"),
            dict(end_date="2026-11-01"),
            dict(ad_groups=[self._ag(headlines=["a", "b"])]),
            dict(ad_groups=[self._ag(headlines=["x" * 31, "b", "c"])]),
            dict(ad_groups=[self._ag(final_url="example.com")]),
            dict(schedule=[{"day": "XYZ", "start_hour": 8, "end_hour": 18}]),
        ]
        for over in bad:
            with self.subTest(over=list(over)):
                with self.assertRaises(ToolError):
                    self._call(**over)
