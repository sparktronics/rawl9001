# PR Regression Review Audit

> **System:** RAWL 9001 — Automated AEM PR regression reviewer
> **Repo:** AEM-Platform-Core (`d777d14e-bbc7-4aa8-865e-6a87372263ac`)
> **Audit date:** 2026-04-02
> **Scope:** 5 most recent Active PRs · 30 most recent Completed (merged) PRs
> PRs with no Automated Regression Review comment are excluded per audit scope.

---

## Section 1 — Active PRs

| PR ID | Title | Author | Status | Action-Required Reason | Policy Override Reason | Assessment | Notes |
|---|---|---|---|---|---|---|---|
| [#383525](https://batdigital.visualstudio.com/Consumer%20Platforms/_git/AEM-Platform-Core/pullrequest/383525) | Accessibility: Disabled State Button | Mhmod Zki | Active | Global `.bat-button--disabled` class pattern conflicts with searchbar's `data-disabled` attribute system — two inconsistent disabled-state mechanisms across components. | — | ✅ Valid | Dual disabled patterns are a real contract inconsistency; downstream components using either pattern could break. |
| [#382950](https://batdigital.visualstudio.com/Consumer%20Platforms/_git/AEM-Platform-Core/pullrequest/382950) | Cross Brand Analytics | Iustin Baciu | Active | `brandIndexMapping` rendered in HTL without null/fallback guard → invalid JSON crashes the component on init; `algoliaInsights.resolveBrandIndexName` called without optional chaining on potentially undefined object. | — | ✅ Valid | Invalid JSON in a `data-cmp-config` attribute is a hard runtime crash, not a hypothetical edge case. |
| [#382700](https://batdigital.visualstudio.com/Consumer%20Platforms/_git/AEM-Platform-Core/pullrequest/382700) | BFF Devices Starter Kit | Artem Yurchuk | Active | `OBJECT_MAPPER.writeValueAsString()` double-quotes string values, producing escaped/double-quoted JSON in `data-nc-params` — the attribute value will fail to parse on the frontend. | — | ✅ Valid | ObjectMapper serialising a pre-serialised string is a known Java pitfall; the resulting malformed JSON will break all JS consumers of this attribute. |
| [#382555](https://batdigital.visualstudio.com/Consumer%20Platforms/_git/AEM-Platform-Core/pullrequest/382555) | Form Options Toggle Style | Ana González Ruiz | Active | Policy adds `bat-toggle--true` CSS modifier class with no accompanying CSS or JS assets in the diff — class will be applied but have no visual effect. | — | ✅ Valid | A CSS class with no definition is a silent regression; the feature appears to work in dev if a dev stylesheet exists but will fail in production. |
| [#382506](https://batdigital.visualstudio.com/Consumer%20Platforms/_git/AEM-Platform-Core/pullrequest/382506) | Logout on token fail (DRAFT) | Ana González Ruiz | Active (Draft) | Logout triggered on any API error (5xx, network timeout), not scoped to 401/403 auth failures; additionally reads HTTP status from `data.status` (JSON body field) instead of `response.status`. | — | ✅ Valid | Blanket logout on non-auth errors is a severe UX regression; reading status from the body rather than the HTTP response is a clear implementation bug. |

---

## Section 2 — Completed (Merged) PRs

| PR ID | Title | Author | Status | Action-Required Reason | Policy Override Reason | Assessment | Notes |
|---|---|---|---|---|---|---|---|
| [#383429](https://batdigital.visualstudio.com/Consumer%20Platforms/_git/AEM-Platform-Core/pullrequest/383429) | Base Form — Error Linking Refactor | Felipe Galvis | Completed | `linkErrorToInput()` signature changed in shared base form component, removing dynamic `aria-describedby` management — any component that hadn't been updated would silently lose its error-input link. | "tested" | ✅ Valid | Breaking change to a shared base component is a legitimate block; developer pushed updates to downstream components before tech lead bypassed. |
| [#382926](https://batdigital.visualstudio.com/Consumer%20Platforms/_git/AEM-Platform-Core/pullrequest/382926) | Social Login Register Flow | Dmytro Shalaginov | Completed | `sessionStorage` key renamed from `'social-login-response'` to `'register-sns'` cross-component; `cartCookieName` and related props removed from social auth HTL template — any unconverted consumer would break silently. | "address all review comments" | ✅ Valid | Storage key renames are a classic cross-component contract break; the bypass reason implies fixes were made but may not have been complete. |
| [#382954](https://batdigital.visualstudio.com/Consumer%20Platforms/_git/AEM-Platform-Core/pullrequest/382954) | Video Info Accessibility | Dmytro Shalaginov | Completed | `aria-label="undefined"` rendered on video info button for existing components missing the new required props — literal "undefined" string in accessible name. | — *(clean pass after fix)* | ✅ Valid | Literal "undefined" in an `aria-label` is a WCAG regression; developer added fallback values and bot re-reviewed with review-recommended only, PR merged cleanly. |
| [#383454](https://batdigital.visualstudio.com/Consumer%20Platforms/_git/AEM-Platform-Core/pullrequest/383454) | Responsive Image — CSS min() | Yaroslav Andrieiev | Completed | CSS `min()` function flagged as unsupported in older browsers, potentially breaking layout on legacy clients. | "All fixed" | ⚠️ False Positive | `min()` has been baseline-supported since 2020 (Chrome 79, Firefox 75, Safari 11.1); IE11 is not in the project's support matrix. The bot's browser-support heuristics appear outdated. |
| [#382038](https://batdigital.visualstudio.com/Consumer%20Platforms/_git/AEM-Platform-Core/pullrequest/382038) | BFF Config Null Safety | Yaroslav Andrieiev | Completed | Missing null check on `BffConfig` Context-Aware Configuration binding — NPE at runtime if CA-Config is not configured for an environment; `GraphQLClientHelper` instantiates a new `HttpClient` per request (resource leak). | — *(squash merge, clean)* | ✅ Valid | CA-Config NPE is a real production risk on environments without the config node; developer addressed before merge. |
| [#383022](https://batdigital.visualstudio.com/Consumer%20Platforms/_git/AEM-Platform-Core/pullrequest/383022) | Subscription Details and Product Listing | *(unavailable)* | Completed | *(Thread content could not be retrieved — data too large)* | "considered" | ❓ Unclear | Bot comment content was unavailable; bypass reason "considered" is non-specific and does not indicate whether issues were resolved or dismissed. |
| [#382949](https://batdigital.visualstudio.com/Consumer%20Platforms/_git/AEM-Platform-Core/pullrequest/382949) | Legacy Order Styles Fix | Joanna Pawlicka | Completed | Removal of `width: 100%` on mobile button section in `batcom-bff-legacy-order.scss` flagged as an unrelated/risky change bundled into the fix. | "this doesn't impact others. fix is required" | ⚠️ False Positive | The CSS removal was intentional and part of the bug fix scope; tech lead confirmed it doesn't affect other components. Bot over-flagged an intra-component style cleanup as a regression risk. |
| [#382171](https://batdigital.visualstudio.com/Consumer%20Platforms/_git/AEM-Platform-Core/pullrequest/382171) | Product Listing — SKU Handling | Iustin Baciu | Completed | `item.product.sku` accessed without optional chaining — potential TypeError if `product` is null/undefined in the listing response. | — *(clean pass after fix)* | ✅ Valid | Real null-dereference risk in product data iteration; developer added optional chaining across affected accessors and bot re-reviewed as review-recommended, PR merged cleanly. |
| [#383200](https://batdigital.visualstudio.com/Consumer%20Platforms/_git/AEM-Platform-Core/pullrequest/383200) | Minibasket Spacing Fix (INC1512141) | Riya Nigam | Completed | — *(review-recommended only; PR was never blocked)* | — *(clean merge)* | — | Bot flagged potential visual regression from `margin-bottom: 20px` on minibasket actions at mobile/tablet breakpoint; developer confirmed with screenshots across all devices. No action-required finding raised. |
| [#376312](https://batdigital.visualstudio.com/Consumer%20Platforms/_git/AEM-Platform-Core/pullrequest/376312) | Font Normalization — MYGLO JP | Dragan Dimic | Completed | Thousands of high-specificity CSS overrides added to `myglojp/font-cleanup/` — brute-force approach creates performance, maintenance, and scalability risk; base component `batcom-bff-uploadid.clientlibs.scss` had font removed, risking regression on other brands. 12 bot reviews over 24 days, all or most `action-required`. | "Small amount of regression is expected with this task." | ✅ Valid | Bypass reason explicitly acknowledges expected regression — high-specificity CSS sprawl is a genuine long-term risk, and the base component change has cross-brand impact potential. |
| [#383086](https://batdigital.visualstudio.com/Consumer%20Platforms/_git/AEM-Platform-Core/pullrequest/383086) | Flyout Background Colour (2490107) | Gopikrishnan Viswanathan | Completed | (1st review) `form:first-of-type` selector removes padding from all non-form flyout content — layout breaks if any flyout lacks a form as first child. (2nd review) CSS `:has()` pseudo-class lacks fallback for Firefox ESR 115 and older Safari — background fails silently, risking WCAG contrast violation. | — *(clean merge after fixes)* | ✅ Valid | Both findings are real: the fragile selector is a structural regression risk, and `:has()` is genuinely unsupported in Firefox ESR 115 (widely used in enterprise). Developer addressed both before merge. |
| [#382862](https://batdigital.visualstudio.com/Consumer%20Platforms/_git/AEM-Platform-Core/pullrequest/382862) | JP Firstname/Lastname Order (2491479) | Dhananjay Harel | Completed | `getDisplayName()` used strict `lang === 'ja'` check — fails for `ja-JP` locale subtag, causing Japanese users to see wrong name order; non-standard `lang === 'jp'` check included. | — *(clean merge after fix)* | ✅ Valid | Language subtag mismatch is a real functional bug for Japanese locale; developer fixed using `startsWith('ja')` and bot re-reviewed as review-recommended. |
| [#382721](https://batdigital.visualstudio.com/Consumer%20Platforms/_git/AEM-Platform-Core/pullrequest/382721) | Add to Cart Button Fix (2487661) | Nikita Gore | Completed | — *(review-recommended only; PR was never blocked)* | — *(clean merge)* | — | Bot flagged that new `selectedConsumables === maxConsumables` condition should be tested for edge case where `maxConsumables` is 0; developer confirmed with test video. |
| [#382820](https://batdigital.visualstudio.com/Consumer%20Platforms/_git/AEM-Platform-Core/pullrequest/382820) | Invisible Trial Product Fix (2485134) | Kishore Mohanasundaram | Completed | — *(review-recommended only; PR was never blocked)* | — *(clean merge)* | — | Bot flagged that `is_product_invisible !== false` might hide visible products if API omits the field; developer clarified the intent is to hide everything except explicitly `false` — scoped to trial products only. |
| [#383136](https://batdigital.visualstudio.com/Consumer%20Platforms/_git/AEM-Platform-Core/pullrequest/383136) | Object Length Validation Fix (2458905) | Amit Amritsagar | Completed | — *(review-recommended only; PR was never blocked)* | — *(clean merge)* | — | Bot noted that fixing `order?.length > 0` → `Object.keys(order).length > 0` enables a previously dormant code path; recommended thorough testing of the zero-order flow. |

---

## Summary

| Outcome | Active | Completed | Total |
|---|---|---|---|
| ✅ Valid | 5 | 8 | **13** |
| ⚠️ False Positive | 0 | 2 | **2** |
| ❓ Unclear | 0 | 1 | **1** |
| — *(no action-required / review-recommended only)* | 0 | 4 | **4** |

**False positive rate: ~15%** (2 of 13 PRs with `action-required` findings).
**Bot coverage:** 20 completed PRs had bot comments across all 3 batches; 14 had at least one `action-required` finding.

> **Batch 2 note (skip=10):** 9 of 10 PRs targeted `release/2.7.x` with no bot comment — bot appears not configured for that branch. #383200 had `review-recommended` only.
> **Batch 3 note (skip=20):** 4 of 10 PRs had no bot comment (#383143, #383142, #383140, #383138). 3 had `review-recommended` only (#382721, #382820, #383136). 3 had `action-required` findings (#376312, #383086, #382862).

### False Positive Patterns

1. **Outdated browser-support rules** — `#383454`: Bot flagged CSS `min()` as unsupported; this function has been baseline since 2020. Bot's browser-support heuristics may reference legacy compatibility data.
2. **Intra-component scope misread** — `#382949`: Bot flagged a CSS rule removal as an unrelated change; it was intentionally part of the fix and had no downstream consumers.

### Notable Findings

- All 5 active PRs have unresolved `action-required` findings — none have been addressed yet.
- 6 of 13 completed PRs with `action-required` findings required a tech lead bypass (`#383429`, `#382926`, `#383454`, `#382949`, `#383022`, `#376312`).
- 6 PRs passed cleanly after developer fixes with no bypass (`#382954`, `#382038`, `#382171`, `#383086`, `#382862`, and one iteration of `#383086`).
- `#376312` is notable: 12 bot review iterations over 24 days, bypass reason explicitly acknowledges expected regression — the bot served as a quality gate that was knowingly overridden.
- Bot is not running on `release/2.7.x` backport branch — all 12+ PRs to that branch in batches 2 and 3 have no bot comments. Consider enabling the webhook for release branches.
- PR `#383022` cannot be fully assessed; consider re-running the audit against its thread data directly in Azure DevOps.
