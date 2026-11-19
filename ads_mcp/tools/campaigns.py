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

"""Tools for controlling Campaigns (status and daily budget) in Google Ads."""

from enum import Enum

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ads_mcp import propose_and_confirm as pac
import ads_mcp.utils as utils

campaigns_mcp = FastMCP("campaigns")


class CampaignStatus(str, Enum):
    """Campaign statuses that can be set through this server."""

    ENABLED = "ENABLED"
    PAUSED = "PAUSED"


def _clean_campaign_id(campaign_id: str | int) -> str:
    cleaned = "".join(ch for ch in str(campaign_id) if ch.isdigit())
    if not cleaned:
        raise ToolError("Error: 'campaign_id' must contain digits.")
    return cleaned


def _fetch_campaign(ga_client, customer_id: str, campaign_id: str):
    """Returns the first GoogleAdsRow for the campaign, or raises ToolError."""
    ga_service = utils.get_googleads_service_from_client(
        ga_client, "GoogleAdsService"
    )
    query = (
        "SELECT campaign.id, campaign.name, campaign.status, "
        "campaign.campaign_budget, campaign_budget.amount_micros "
        f"FROM campaign WHERE campaign.id = {campaign_id}"
    )
    for row in ga_service.search(customer_id=customer_id, query=query):
        return row
    raise ToolError(
        f"Error: Campaign '{campaign_id}' not found in customer '{customer_id}'."
    )


@campaigns_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True)
)
def update_campaign_status(
    customer_id: str | int,
    campaign_id: str | int,
    status: CampaignStatus,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Pauses or enables a campaign.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call `update_campaign_status` with `validate_only=True` (the default). The tool returns structured `change_items` (including the current status) and a `checksum` without committing changes.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by the previous call.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        campaign_id: The numeric ID of the campaign.
        status: 'ENABLED' or 'PAUSED'.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal.
        checksum: Checksum from a prior `validate_only=True` call. Required when `validate_only=False`.
    """
    cleaned_customer_id = utils.clean_customer_id(customer_id)
    if not cleaned_customer_id:
        raise ToolError("Error: 'customer_id' must contain digits.")
    cleaned_campaign_id = _clean_campaign_id(campaign_id)

    status_upper = (
        status.value if isinstance(status, Enum) else str(status).upper()
    )
    if status_upper not in CampaignStatus.__members__:
        raise ToolError(
            f"Error: Invalid status '{status}'. Must be ENABLED or PAUSED."
        )

    ga_client = utils.get_googleads_client(login_customer_id=login_customer_id)
    row = _fetch_campaign(ga_client, cleaned_customer_id, cleaned_campaign_id)
    current_status = row.campaign.status
    current_status_name = getattr(current_status, "name", str(current_status))
    campaign_name = row.campaign.name

    target_resource_name = (
        f"customers/{cleaned_customer_id}/campaigns/{cleaned_campaign_id}"
    )
    campaign_service = utils.get_googleads_service_from_client(
        ga_client, "CampaignService"
    )
    op = ga_client.get_type("CampaignOperation")
    op.update.resource_name = target_resource_name
    op.update.status = getattr(
        ga_client.enums.CampaignStatusEnum, status_upper
    )
    op.update_mask.paths.append("status")

    operation_payload = {
        "tool": "update_campaign_status",
        "campaign_id": cleaned_campaign_id,
        "status": status_upper,
    }

    change_items = pac.build_change_items(
        operation="UPDATE",
        entity_readable=f"Campaign '{campaign_name}'",
        primary_key_readable=target_resource_name,
        fields={"Status": status_upper},
        original_fields={"Status": current_status_name},
    )

    request = ga_client.get_type("MutateCampaignsRequest")
    request.customer_id = cleaned_customer_id
    request.operations.append(op)

    def _validate() -> None:
        request.validate_only = True
        campaign_service.mutate_campaigns(request=request)

    def _commit() -> str:
        request.validate_only = False
        response = campaign_service.mutate_campaigns(request=request)
        res_id = utils.extract_id(response.results[0].resource_name)
        return (
            f"Successfully set campaign {res_id} "
            f"('{campaign_name}') to {status_upper}."
        )

    return pac.propose_and_confirm(
        customer_id=cleaned_customer_id,
        validate_only=validate_only,
        checksum=checksum,
        operation_payload=operation_payload,
        change_items=change_items,
        validate_fn=_validate,
        commit_fn=_commit,
        error_prefix="Failed to update campaign status",
    )


@campaigns_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True)
)
def update_campaign_budget(
    customer_id: str | int,
    campaign_id: str | int,
    daily_budget: float,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Changes the daily budget of a campaign's budget resource.

    Note: if the budget is shared by several campaigns, all of them are affected.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call `update_campaign_budget` with `validate_only=True` (the default). The tool returns structured `change_items` (including the current budget) and a `checksum` without committing changes.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by the previous call.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        campaign_id: The numeric ID of the campaign.
        daily_budget: New daily budget in the account's currency units (e.g. 25.50). Must be > 0.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal.
        checksum: Checksum from a prior `validate_only=True` call. Required when `validate_only=False`.
    """
    cleaned_customer_id = utils.clean_customer_id(customer_id)
    if not cleaned_customer_id:
        raise ToolError("Error: 'customer_id' must contain digits.")
    cleaned_campaign_id = _clean_campaign_id(campaign_id)
    if daily_budget <= 0:
        raise ToolError("Error: 'daily_budget' must be greater than 0.")

    new_micros = int(round(daily_budget * 1_000_000))

    ga_client = utils.get_googleads_client(login_customer_id=login_customer_id)
    row = _fetch_campaign(ga_client, cleaned_customer_id, cleaned_campaign_id)
    budget_resource_name = row.campaign.campaign_budget
    if not budget_resource_name:
        raise ToolError(
            f"Error: Campaign '{cleaned_campaign_id}' has no budget resource."
        )
    current_micros = int(row.campaign_budget.amount_micros)
    campaign_name = row.campaign.name

    budget_service = utils.get_googleads_service_from_client(
        ga_client, "CampaignBudgetService"
    )
    op = ga_client.get_type("CampaignBudgetOperation")
    op.update.resource_name = budget_resource_name
    op.update.amount_micros = new_micros
    op.update_mask.paths.append("amount_micros")

    operation_payload = {
        "tool": "update_campaign_budget",
        "campaign_id": cleaned_campaign_id,
        "budget": budget_resource_name,
        "amount_micros": new_micros,
    }

    change_items = pac.build_change_items(
        operation="UPDATE",
        entity_readable=f"Campaign Budget of '{campaign_name}'",
        primary_key_readable=budget_resource_name,
        fields={"Daily Budget": f"{new_micros / 1_000_000:.2f}"},
        original_fields={"Daily Budget": f"{current_micros / 1_000_000:.2f}"},
    )

    request = ga_client.get_type("MutateCampaignBudgetsRequest")
    request.customer_id = cleaned_customer_id
    request.operations.append(op)

    def _validate() -> None:
        request.validate_only = True
        budget_service.mutate_campaign_budgets(request=request)

    def _commit() -> str:
        request.validate_only = False
        response = budget_service.mutate_campaign_budgets(request=request)
        res_id = utils.extract_id(response.results[0].resource_name)
        return (
            f"Successfully set daily budget of campaign "
            f"{cleaned_campaign_id} ('{campaign_name}') to "
            f"{new_micros / 1_000_000:.2f} (budget {res_id})."
        )

    return pac.propose_and_confirm(
        customer_id=cleaned_customer_id,
        validate_only=validate_only,
        checksum=checksum,
        operation_payload=operation_payload,
        change_items=change_items,
        validate_fn=_validate,
        commit_fn=_commit,
        error_prefix="Failed to update campaign budget",
    )


_DAY_NAMES = {
    "MON": "MONDAY",
    "TUE": "TUESDAY",
    "WED": "WEDNESDAY",
    "THU": "THURSDAY",
    "FRI": "FRIDAY",
    "SAT": "SATURDAY",
    "SUN": "SUNDAY",
}
_DEFAULT_SCHEDULE = [
    {"day": d, "start_hour": 8, "end_hour": 18}
    for d in ("MON", "TUE", "WED", "THU", "FRI")
]


def _parse_keyword(raw: str) -> tuple[str, str]:
    """'[x]' -> EXACT, '"x"' -> PHRASE, anything else -> BROAD."""
    text = raw.strip()
    if len(text) > 2 and text.startswith("[") and text.endswith("]"):
        return text[1:-1].strip(), "EXACT"
    if len(text) > 2 and text.startswith('"') and text.endswith('"'):
        return text[1:-1].strip(), "PHRASE"
    return text, "BROAD"


def _check_date(value: str, label: str) -> str:
    from datetime import datetime

    try:
        datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError) as exc:
        raise ToolError(f"Error: '{label}' must be YYYY-MM-DD.") from exc
    return value


def _validate_ad_group(ag: dict, idx: int) -> None:
    where = f"ad_groups[{idx}]"
    if not ag.get("name"):
        raise ToolError(f"Error: {where} needs a 'name'.")
    if not ag.get("keywords"):
        raise ToolError(f"Error: {where} needs at least one keyword.")
    heads = ag.get("headlines") or []
    descs = ag.get("descriptions") or []
    if not 3 <= len(heads) <= 15:
        raise ToolError(f"Error: {where} needs 3-15 headlines.")
    if not 2 <= len(descs) <= 4:
        raise ToolError(f"Error: {where} needs 2-4 descriptions.")
    if any(not h or len(h) > 30 for h in heads):
        raise ToolError(f"Error: {where} headlines must be 1-30 characters.")
    if any(not d or len(d) > 90 for d in descs):
        raise ToolError(
            f"Error: {where} descriptions must be 1-90 characters."
        )
    url = ag.get("final_url", "")
    if not str(url).startswith(("http://", "https://")):
        raise ToolError(f"Error: {where} 'final_url' must be http(s).")
    for p in ("path1", "path2"):
        if len(ag.get(p) or "") > 15:
            raise ToolError(f"Error: {where} '{p}' max 15 characters.")


@campaigns_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def create_search_campaign(
    customer_id: str | int,
    name: str,
    daily_budget: float,
    geo_target_ids: list[int],
    ad_groups: list[dict],
    start_date: str,
    end_date: str,
    max_cpc: float | None = None,
    language_ids: list[int] | None = None,
    schedule: list[dict] | None = None,
    negative_keywords: list[str] | None = None,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Creates a complete Search campaign, ALWAYS in PAUSED status.

    One atomic request creates: budget, campaign (Google Search only, no search
    partners/Display, Maximize Clicks), location + language + ad schedule
    targeting, negative keywords, and per ad group: keywords and a Responsive
    Search Ad. Nothing is enabled; use `update_campaign_status` afterwards.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (default). The API validates the whole request and the tool returns `change_items` and a `checksum`; nothing is created.
    2. Present the `change_items` to the user and wait for explicit approval.
    3. Only then call again with `validate_only=False`, the exact same parameters and the `checksum`.

    Args:
        customer_id: Google Ads Customer ID.
        name: Campaign name.
        daily_budget: Daily budget in account currency. For a total budget T over N active days use T/N and set `end_date`.
        geo_target_ids: Geo target constant IDs (see `suggest_geo_targets`). Targets people PRESENT in these locations.
        ad_groups: List of dicts: `name`, `keywords` (list of strings; "text" = phrase, [text] = exact, bare = broad), `headlines` (3-15, <=30 chars), `descriptions` (2-4, <=90 chars), `final_url`, optional `path1`, `path2`.
        start_date: YYYY-MM-DD.
        end_date: YYYY-MM-DD (campaign stops at end of this day).
        max_cpc: Optional max CPC cap in account currency for Maximize Clicks.
        language_ids: Language constant IDs; default [1003] (Spanish).
        schedule: Optional list of dicts `day` (MON..SUN), `start_hour`, `end_hour` (0-24). Default Mon-Fri 8-18 (account time zone).
        negative_keywords: Campaign-level negative keywords (phrase match).
        login_customer_id: Optional manager customer ID.
        validate_only: If True (default) only validates.
        checksum: Checksum from the validate_only=True call.
    """
    cid = utils.clean_customer_id(customer_id)
    if not cid:
        raise ToolError("Error: 'customer_id' must contain digits.")
    if not name or not name.strip():
        raise ToolError("Error: 'name' is required.")
    if daily_budget <= 0:
        raise ToolError("Error: 'daily_budget' must be greater than 0.")
    if max_cpc is not None and max_cpc <= 0:
        raise ToolError("Error: 'max_cpc' must be greater than 0.")
    if not geo_target_ids:
        raise ToolError("Error: 'geo_target_ids' is required.")
    if not ad_groups:
        raise ToolError("Error: at least one ad group is required.")
    _check_date(start_date, "start_date")
    _check_date(end_date, "end_date")
    if end_date < start_date:
        raise ToolError("Error: 'end_date' is before 'start_date'.")
    for i, ag in enumerate(ad_groups):
        _validate_ad_group(ag, i)

    languages = list(language_ids or [1003])
    sched = list(schedule if schedule is not None else _DEFAULT_SCHEDULE)
    for s in sched:
        day = str(s.get("day", "")).upper()[:3]
        if day not in _DAY_NAMES:
            raise ToolError(f"Error: invalid schedule day '{s.get('day')}'.")
        if not 0 <= int(s["start_hour"]) < int(s["end_hour"]) <= 24:
            raise ToolError("Error: schedule needs 0<=start_hour<end_hour<=24.")
    negatives = [n.strip() for n in (negative_keywords or []) if n.strip()]
    budget_micros = int(round(daily_budget * 1_000_000))
    cpc_micros = int(round(max_cpc * 1_000_000)) if max_cpc else None

    ga_client = utils.get_googleads_client(login_customer_id=login_customer_id)
    enums = ga_client.enums
    ga_service = utils.get_googleads_service_from_client(
        ga_client, "GoogleAdsService"
    )
    budget_rn = f"customers/{cid}/campaignBudgets/-1"
    campaign_rn = f"customers/{cid}/campaigns/-2"
    ops = []

    def new_op():
        return ga_client.get_type("MutateOperation")

    op = new_op()
    b = op.campaign_budget_operation.create
    b.resource_name = budget_rn
    b.name = f"{name} - presupuesto"
    b.amount_micros = budget_micros
    b.delivery_method = enums.BudgetDeliveryMethodEnum.STANDARD
    b.explicitly_shared = False
    ops.append(op)

    op = new_op()
    c = op.campaign_operation.create
    c.resource_name = campaign_rn
    c.name = name
    c.status = enums.CampaignStatusEnum.PAUSED
    c.advertising_channel_type = enums.AdvertisingChannelTypeEnum.SEARCH
    c.campaign_budget = budget_rn
    c.network_settings.target_google_search = True
    c.network_settings.target_search_network = False
    c.network_settings.target_content_network = False
    c.geo_target_type_setting.positive_geo_target_type = (
        enums.PositiveGeoTargetTypeEnum.PRESENCE
    )
    c.contains_eu_political_advertising = (
        enums.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
    )
    c.target_spend.cpc_bid_ceiling_micros = cpc_micros or 0
    c.start_date_time = f"{start_date} 00:00:00"
    c.end_date_time = f"{end_date} 23:59:59"
    ops.append(op)

    for gid in geo_target_ids:
        op = new_op()
        cc = op.campaign_criterion_operation.create
        cc.campaign = campaign_rn
        cc.location.geo_target_constant = f"geoTargetConstants/{int(gid)}"
        ops.append(op)
    for lid in languages:
        op = new_op()
        cc = op.campaign_criterion_operation.create
        cc.campaign = campaign_rn
        cc.language.language_constant = f"languageConstants/{int(lid)}"
        ops.append(op)
    for s in sched:
        op = new_op()
        cc = op.campaign_criterion_operation.create
        cc.campaign = campaign_rn
        cc.ad_schedule.day_of_week = getattr(
            enums.DayOfWeekEnum, _DAY_NAMES[str(s["day"]).upper()[:3]]
        )
        cc.ad_schedule.start_hour = int(s["start_hour"])
        cc.ad_schedule.end_hour = int(s["end_hour"])
        cc.ad_schedule.start_minute = enums.MinuteOfHourEnum.ZERO
        cc.ad_schedule.end_minute = enums.MinuteOfHourEnum.ZERO
        ops.append(op)
    for neg in negatives:
        op = new_op()
        cc = op.campaign_criterion_operation.create
        cc.campaign = campaign_rn
        cc.negative = True
        cc.keyword.text = neg
        cc.keyword.match_type = enums.KeywordMatchTypeEnum.PHRASE
        ops.append(op)

    n_keywords = 0
    for i, ag in enumerate(ad_groups):
        ag_rn = f"customers/{cid}/adGroups/{-(10 + i)}"
        op = new_op()
        g = op.ad_group_operation.create
        g.resource_name = ag_rn
        g.name = ag["name"]
        g.campaign = campaign_rn
        g.status = enums.AdGroupStatusEnum.ENABLED
        g.type_ = enums.AdGroupTypeEnum.SEARCH_STANDARD
        ops.append(op)
        for raw in ag["keywords"]:
            text, mtype = _parse_keyword(raw)
            op = new_op()
            k = op.ad_group_criterion_operation.create
            k.ad_group = ag_rn
            k.status = enums.AdGroupCriterionStatusEnum.ENABLED
            k.keyword.text = text
            k.keyword.match_type = getattr(enums.KeywordMatchTypeEnum, mtype)
            ops.append(op)
            n_keywords += 1
        op = new_op()
        a = op.ad_group_ad_operation.create
        a.ad_group = ag_rn
        a.status = enums.AdGroupAdStatusEnum.ENABLED
        a.ad.final_urls.append(ag["final_url"])
        rsa = a.ad.responsive_search_ad
        for h in ag["headlines"]:
            asset = ga_client.get_type("AdTextAsset")
            asset.text = h
            rsa.headlines.append(asset)
        for d in ag["descriptions"]:
            asset = ga_client.get_type("AdTextAsset")
            asset.text = d
            rsa.descriptions.append(asset)
        if ag.get("path1"):
            rsa.path1 = ag["path1"]
        if ag.get("path2"):
            rsa.path2 = ag["path2"]
        ops.append(op)

    operation_payload = {
        "tool": "create_search_campaign",
        "name": name,
        "daily_budget_micros": budget_micros,
        "max_cpc_micros": cpc_micros,
        "geo_target_ids": [int(g) for g in geo_target_ids],
        "language_ids": [int(x) for x in languages],
        "schedule": [
            {
                "day": str(s["day"]).upper()[:3],
                "start_hour": int(s["start_hour"]),
                "end_hour": int(s["end_hour"]),
            }
            for s in sched
        ],
        "negative_keywords": negatives,
        "start_date": start_date,
        "end_date": end_date,
        "ad_groups": ad_groups,
    }

    fields = {
        "Name": name,
        "Status": "PAUSED",
        "Daily Budget": f"{daily_budget:.2f}",
        "Dates": f"{start_date} -> {end_date}",
        "Max CPC": f"{max_cpc:.2f}" if max_cpc else "automatic",
        "Locations": f"{len(geo_target_ids)} geo targets (presence)",
        "Languages": ", ".join(str(x) for x in languages),
        "Schedule": ", ".join(
            f"{str(s['day']).upper()[:3]} {s['start_hour']}-{s['end_hour']}"
            for s in sched
        ),
        "Negative keywords": str(len(negatives)),
        "Ad groups": ", ".join(ag["name"] for ag in ad_groups),
        "Keywords": str(n_keywords),
        "Ads": f"{len(ad_groups)} responsive search ad(s)",
    }
    change_items = pac.build_change_items(
        operation="CREATE",
        entity_readable="Search campaign",
        primary_key_readable=campaign_rn,
        fields=fields,
    )

    request = ga_client.get_type("MutateGoogleAdsRequest")
    request.customer_id = cid
    request.mutate_operations.extend(ops)
    request.partial_failure = False

    def _validate() -> None:
        request.validate_only = True
        ga_service.mutate(request=request)

    def _commit() -> str:
        request.validate_only = False
        response = ga_service.mutate(request=request)
        camp_id = ""
        for r in response.mutate_operation_responses:
            if r.campaign_result.resource_name:
                camp_id = utils.extract_id(r.campaign_result.resource_name)
        return (
            f"Successfully created PAUSED campaign {camp_id} ('{name}') with "
            f"{len(ad_groups)} ad group(s) and {n_keywords} keyword(s). "
            "Review it in Google Ads, then enable it with "
            "update_campaign_status."
        )

    return pac.propose_and_confirm(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        operation_payload=operation_payload,
        change_items=change_items,
        validate_fn=_validate,
        commit_fn=_commit,
        error_prefix="Failed to create search campaign",
    )


@campaigns_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False)
)
def suggest_geo_targets(
    customer_id: str | int,
    names: list[str],
    country_code: str = "CO",
    locale: str = "es",
    login_customer_id: str | int | None = None,
) -> str:
    """Finds geo target constant IDs (cities, neighborhoods...) by name.

    Use the returned IDs in `create_search_campaign.geo_target_ids`.

    Args:
        customer_id: Google Ads Customer ID.
        names: Location names, e.g. ["Pance, Cali", "Ciudad Jardin, Cali"].
        country_code: ISO country code to restrict results (default CO).
        locale: Language for names (default es).
        login_customer_id: Optional manager customer ID.
    """
    import json

    cid = utils.clean_customer_id(customer_id)
    if not cid:
        raise ToolError("Error: 'customer_id' must contain digits.")
    ga_client = utils.get_googleads_client(login_customer_id=login_customer_id)
    service = utils.get_googleads_service_from_client(
        ga_client, "GeoTargetConstantService"
    )
    request = ga_client.get_type("SuggestGeoTargetConstantsRequest")
    request.locale = locale
    request.country_code = country_code
    request.location_names.names.extend(names)
    try:
        response = service.suggest_geo_target_constants(request=request)
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"Failed to suggest geo targets: {exc}") from exc
    out = []
    for s in response.geo_target_constant_suggestions:
        g = s.geo_target_constant
        out.append(
            {
                "search_term": s.search_term,
                "id": int(g.id),
                "name": g.name,
                "canonical_name": g.canonical_name,
                "target_type": g.target_type,
                "status": getattr(g.status, "name", str(g.status)),
            }
        )
    return json.dumps(out, indent=2, ensure_ascii=False)
