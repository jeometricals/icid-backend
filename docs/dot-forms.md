# NYC DOT's fillable forms

ICID fills two of NYC DOT's fillable PDFs from a ped ramp corner's data, and leaves them **fillable**: DOT asks for
"pdf fillable format" and refuses hand-written or scanned forms, so nothing is ever flattened. The values go into the
AcroForm's own fields; pypdf generates each field's appearance, and `NeedAppearances` is set so Acrobat and Chrome
redraw them from the values (and run DOT's own format scripts) when the file is opened.

| Form | Template | Service |
|---|---|---|
| Pedestrian Ramp Inspection Form v1.6 (09-17-20) | `templates/dot_ped_ramp_inspection_v1.6.pdf` (14 pages, 208 fields) | `api/services/dot_inspection_pdf.py`: `fill_inspection_forms(corner, ramps, photos) -> InspectionPdfs` |
| Technical Infeasibility Form v1.9 (09-25-20) | `templates/dot_tif_v1.9.pdf` (9 pages, 74 fields) | `api/services/dot_tif_pdf.py`: `fill_tif_form(tif, corner, ramp, photos, layout, signature_png) -> bytes` |

Both take plain dicts (the rows' columns) and image bytes, and return PDF bytes: no database, no Storage.
`api/services/dot_pdf_fill.py` holds what both use (`DotForm`: text, dropdowns, radio and check boxes, image buttons,
drawing on a page; `prepare_image`; `format_number`). Each form's field map is data in its own module.

The templates are DOT's files unchanged. At import each module logs **one warning** listing the template's fields its
map never fills ("Unmapped DOT fields in ..."), and another if the map names a field the template lacks (a new DOT
version). Today's warnings:

```
Unmapped DOT fields in dot_ped_ramp_inspection_v1.6.pdf (6): CT_RAMP LOCATION_1, CT_RAMP LOCATION_2, CT_RAMP LOCATION_3, CT_RAMP LOCATION_4, RAMP LOCATION_1, RAMP LOCATION_2
Unmapped DOT fields in dot_tif_v1.9.pdf (9): APPROVED, COMMENTS, DATE_2, DENIED, NYC DOTDDC REPRESENTATIVE, RESUBMIT SEE COMMENTS BELOW, SIGNATURE, SIGNATURE_2, TITLE
```

## Values

- **Numbers** print the number alone (the form prints `%` or `IN.` after it), without trailing zeros: `8.30` is `8.3`,
  `48.0` is `48`. A blank stays blank. DOT's format scripts (`AFNumber_Format(1, ...)`) may show one decimal place
  (`48.0`) once Acrobat redraws the field; the value is the same.
- **Dates** follow each field's format script: `m/d/yy` on the inspection form, `mm/dd/yyyy` on the TIF.
- **Dropdowns** take DOT's export value; the field shows the option's display text (`Bronx` shows `The Bronx`). A value
  missing from the dropdown is a programming error (`ValueError`), not a blank.
- **Radio pairs** are `/TRUE` / `/FALSE` (yes / no), `/YES` `/NO` `/N/A` (DWS aligned). `None` leaves both boxes empty.
- **Fields shown twice** (a measurement on the ramp diagram and in the list) are one field with two widgets: both show
  the value.

### Choice values, ICID to DOT

| ICID column | ICID value → DOT export value |
|---|---|
| `entity` | `ddc_city_agency_capital` → DDC City Agency Sponsored Capital Project; `ddc_landmark_ped_ramp` → DDC Landmark Ped Ramp Contract; `ddc_dot_ped_ramp` → DDC Managed DOT Ped Ramp Contract; `ddc_prior_notice` → DDC Prior Notice Contract; `ddc_ta_ped_ramp` → DDC TA Ped Ramp Contract; `dep` `dob` `dpr` `edc` `mta` `nysdot` `sca` → DEP DOB DPR EDC MTA NYSDOT SCA; `other` → Other |
| `boro` | as stored (`Manhattan`, `Bronx`, `Brooklyn`, `Queens`, `Staten Island`); `Bronx` displays "The Bronx" |
| `traffic_control_ns` / `_ew` | `signal` → SIGNAL, `signal_aps` → APS, `stop_yield` → SIGN, `none` → NONE |
| `corner_type` | radio `CORNER_TYPE`: `intersection_corner` → /CORNER, `island_median` → /ISLAND-MEDIAN, `mid_block` → /MID BLOCK, `top_of_t` → /TOP TEE |
| `corner_direction` | as stored (SW NW NE SE N S E W) |
| `contract_type` | radio: `simple` → /SIMPLE, `complex` → /COMPLEX |
| `curb_material` | `steel_faced` → STEEL FACED CONCRETE, `concrete` → CONCRETE, `concrete_and_gutter` → CONCRETE CURB AND GUTTER, `granite` → GRANITE, `bluestone` → BLUESTONE, `other` → OTHER |
| `sidewalk_material` | `concrete` → CONCRETE, `pigmented_concrete` → PIGMENTED CONCRETE, `bluestone` → BLUESTONE, `cobblestone` → COBBLESTONE, `other` → OTHER |
| `dws_color` | `red` → RED, `white` → WHITE, `dark_on_light` → DARK ON LIGHT, `light_on_dark` → LIGHT ON DARK, `other` → OTHER |
| `dws_material` | `preformed_plastic_embedded` → PLASTIC DE, `preformed_plastic_surface_applied` → PLASTIC DSA, `cast_in_place_surface_applied` → CAST IN PLACE SURFACE APPLIED, `cast_iron` → IRON, `stone` → STONE, `concrete_tile` → CONCRETE |
| `construction` | `existing_ramp` → Existing Ramp, `new_install` → New Install, `upgrade` → Upgrade, `ramp_removed` → Ramp Removed |
| `action_taken` | `ada_compliant_constructed` → " ADA Compliant Ramp Constructed" (DOT's value starts with a space), `max_extent_feasible` → Constructed to the Maximum Extent Feasible, `ada_compliant_exists` → ADA Compliant Ramp Exists, `ramp_removed` → Ramp Removed, `other` → Other (Provide Explanation) |
| `dws_aligned_with_ramp` | `yes` → /YES, `no` → /NO, `na` → /N/A |
| booleans | `true` → /TRUE, `false` → /FALSE |
| TIF `project_category` | radio `PROJECT_ENTITY`: `city_agency` → /CITY AGENCY, `state_agency` → /STATE AGENCY, `transit_authority` → /MTA AUTHORITY, `other` → /OTHER ENTITY |
| TIF `ramp_construction` | radio `RAMPCONSTRUCTION`: `new_installation` → /New Installation, `upgrade` → /Upgrade |

`tests/test_dot_inspection_pdf.py` and `tests/test_dot_tif_pdf.py` check every value against the template's own
options and states.

## Inspection form

Pages 1-6 are DOT's instructions; the fields are on pages 7-14. Every copy keeps all 14 pages: a ramp-type page no ramp
uses stays blank.

| Page | What | Filled from |
|---|---|---|
| 7 | Sections 1-4, the corner | the corner (below) |
| 8 | Slot 1, perpendicular ramp (`*_1`) | the ramp's attributes |
| 9 | Slot 1, parallel ramp (`PAR_*_1`) | the ramp's attributes |
| 10 | Slot 1, a cut-through's two ramps (`CT_*_1`, `CT_*_2`) | the pair's attributes |
| 11 | Slot 1's photo log, `Photo_1-1`..`Photo_1-6` | slot 1's ramps' photos |
| 12 | Slot 2, perpendicular ramp (`*_2`) | the ramp's attributes |
| 13 | Slot 2, parallel ramp (`PAR_*_2`) | the ramp's attributes |
| 14 | Slot 2's photo log, `Photo_2-1`..`Photo_2-6` | slot 2's ramp's photos |

### Page 7: the corner

| Field | Corner column |
|---|---|
| `INSPECTED_BY`, `INSPECTION_DATE` | `inspected_by`, `inspection_date` |
| `ENTITY`, `ENTITY_OTHER` | `entity`, `entity_if_other` |
| `BOROUGH`, `CORNER ID`, `CORNER_NUM` | `boro`, `dot_corner_id`, `corner_number` |
| `NORTH-SOUTH_STREET`, `EAST-WEST_STREET` | `north_south_street`, `east_west_street` |
| `TRAFFIC_CONTROL_N-S`, `TRAFFIC_CONTROL_E-W` | `traffic_control_ns`, `traffic_control_ew` |
| `CORNER_TYPE`, `CORNER_DIR` | `corner_type`, `corner_direction` |
| `T_INTERSECTION`, `PAINTED_SPACE` | `t_intersection`, `painted_roadway` |
| `NUM_OF_RAMPS` | `number_of_ramps`, else the count of ramps passed |
| `CONTRACTOR_NAME`, `PERMITTEE_ID`, `FMS_ID`, `PERMIT_NUM` | `contractor_name`, `permittee_id`, `fms_id`, `permit_number` |
| `CONSTRUCTION_COMPLETED`, `CONTRACT_TYPE` | `construction_completion_date`, `contract_type` |
| `CURB_MATERIAL`, `SIDEWALK_MATERIAL` | `curb_material`, `sidewalk_material` |
| `RAMP_TYPE` (section 4) | every ramp type among the corner's ramps |

Section 4's `RAMP_TYPE` is one radio group in DOT's file; a corner with several ramp types ticks each type's box (each
widget's state set on its own, the field's value the first). Every copy repeats page 7 whole, section 4 included.
The corner's `comments` has no field on the form and isn't printed.

### Ramp pages (section 5)

`RAMP_ID` is the ramp's `ramp_id_label` (else its attributes'). `STREET_NAME_1` / `STREET_NAME_2` (slot 2:
`STREET_NAME_1_2` / `STREET_NAME_2_2`), the diagram's street labels, are the ramp's `intersection_street_1` / `_2`;
slot 1's pair is one field on pages 8, 9 and 10, so all three show them. A ramp without attributes gets its ID and street
labels only.

Every type: `PONDING` ← `is_there_ponding`, `RAMP_MAX_CURB_REVEAL` ← `maximum_curb_reveal_at_ramp_in`,
`DWS_LENGTH_24_MIN` ← `dws_length_min_24_inch`, `DWS_FULL_WIDTH` ← `dws_full_ramp_width`,
`DWS_LEFT_CORNERCURB_OFFSET` / `DWS_RIGHT_CORNERCURB_OFFSET` ← `dws_left_offset_from_curb_in` / `dws_right_...`,
`DWS_COLOR`, `DWS_MATERIAL`, `CONSTRUCTION`, `GUTTER_CROSS_SLOPE` / `GUTTER_FLOW_SLOPE` ← `gutter_cross_slope_pct` /
`gutter_flow_slope_pct`.

| Type | Its own fields |
|---|---|
| Perpendicular (no prefix) | `PRP_ACTION_TAKEN` ← `action_taken`; `RAMP_COMMENTS` ← `comments`; `CLEAR_SPACE_IN_CROSSWALK` ← `clear_space_within_crosswalk`; `CLEAR_SPACE_OUTSIDE_TRAVEL_LANE` ← `outside_of_parallel_travel_lane`; `DWS_ALIGNED_WITH_RAMP`; `LEFT_` / `RIGHT_FLARE_TRAVERSABLE_PATH`; `RAMP_RUNNING_SLOPE`, `RAMP_CROSS_SLOPE`, `RAMP_LENGTH`, `RAMP_WIDTH`, `LANDING_RUNNING_SLOPE`, `LANDING_CROSS_SLOPE`, `LANDING_LENGTH`, `LANDING_WIDTH`, `LEFT_` / `RIGHT_FLARE_SLOPE`, `LEFT_` / `RIGHT_SIDEWALK_APPROACH_WIDTH`, `_RUNNING_SLOPE`, `_CROSS_SLOPE` ← the column of the same name (`_pct` or `_in`) |
| Parallel (`PAR_`) | `ACTION_TAKEN`; `RAMP_COMMENTS`; `CLEAR_SPACE_IN_CROSSWALK`; `DWS_ALIGNED_WITH_RAMP`; `LANDING_RUNNING_SLOPE` / `_CROSS_SLOPE` / `_LENGTH` / `_WIDTH` ← `bottom_landing_*`; `LEFT_RAMP_*` ← `left_ramp_*`; `RIGHT_RAMP_*` ← `right_ramp_*` (its cross slope is DOT's `PAR__RIGHT_RAMP_CROSS_SLOPE_n`, two underscores) |
| Cut-through (`CT_`, the pair's ramps `_1` and `_2`) | `ACTION_TAKEN`; `RAMP_RUNNING_SLOPE`, `RAMP_CROSS_SLOPE`, `RAMP_WIDTH`. Once for the pair: `CT_RAMP_LENGTH_1` ← `cut_through_length_in` and `CT_CLEARANCE_BT_DWS_1` ← `min_clearance_between_dws_in` (the first ramp's, else the second's: both rows keep them); `CT_RAMP_COMMENTS_1` ← each ramp's `comments`, each line led by its Ramp ID when both have some |

### Slots and copies

A copy of the form has two ramp slots: slot 1 (pages 8, 9 or 10) and slot 2 (pages 12 or 13; there is no cut-through
page for slot 2). The ramps are taken in the order given:

1. Cut-throughs are paired in order. A pair (or a last, unpaired cut-through, which fills `CT_*_1` only) takes a copy's
   slot 1, so a corner with a cut-through pair has it first.
2. Every other ramp takes the next free slot, on the page of its type.
3. When a copy's slots are used, another copy of the whole form starts, page 7 repeated. A second cut-through pair needs
   its own copy's slot 1.

`fill_inspection_forms` returns one PDF per copy (`InspectionPdfs.files`): a 3-ramp corner downloads as two DOT PDFs,
zipped together. A corner without ramps is one copy with page 7 only filled. Examples: one ramp, one copy (page 8 or 9);
perpendicular + parallel, one copy (pages 8 and 13); three perpendicular, two copies; a cut-through pair + a
perpendicular, one copy (pages 10 and 12); two cut-through pairs, two copies.

### Photos

A slot's six boxes take its ramps' photos together (a cut-through pair's two ramps share slot 1's), sorted by
`photo_number`; past six, the first six by number go in and the rest are counted in `InspectionPdfs.photos_left_out`.
Box *k* is `Photo_{slot}-{k}` (page 14's duplicated `Photo_2-2`, `2-4` and `2-6` widgets are all filled).

- **Each photo** is shrunk first (`prepare_image`): turned upright by its EXIF orientation, at most 1600 px on its long
  edge, flattened onto white, saved as a JPEG at quality 80 (a 4000 x 3000 phone photo comes to about 170-300 KB). HEIC
  is read too.
- **It goes into the button's appearance** (`/AP /N`, a form XObject drawing the image letterboxed in the box) and its
  icon (`/MK /I`, with `/TP 1`), so Acrobat keeps it if it redraws the button. The grey background (`/MK /BG`) is
  removed from a filled box.
- **The caption** ("Photo N: caption", or "Photo N" without one) is printed as page content in Helvetica 7 pt, cut with
  "..." to the box's width, in a 9 pt band **inside the foot of the box**: DOT's boxes touch, so there is no room under
  them. The image is letterboxed above that band.

### Never filled

- **The ramp-location diagram's radios** (`RAMP LOCATION_1`, `RAMP LOCATION_2`, `CT_RAMP LOCATION_1`..`4`):
  `ped_ramp_attributes.ramp_location_code` exists but is NULL for the pilot. The inspector ticks it in Acrobat if DOT
  asks.
- The corner's `comments` (no field).

## Technical Infeasibility Form

Pages 1-4 are instructions; the fields are on pages 5-9.

| Page | Section | Field ← source |
|---|---|---|
| 5 | 1. Submission | `SUBMITTED_BY` ← `submitted_by`; `SUBMISSION DATE` ← `submission_date` (mm/dd/yyyy); `APPLICATION_NUM` ← `application_tracking_no`; `SEQUENCE_NUM` ← `sequence_no`; `PERMITTEE_NAME`, `PERMITTEE_ID`, `BUSINESS_NAME`, `STREET_ADDRESS`, `CITY`, `STATE`, `ZIP_CODE`, `EMAIL`, `PHONE` ← the column of the same name |
| 5 | 2. Project | `PROJECT_ENTITY` ← `project_category`, and `project_category_name` into the matching text field (`CITY_AGENCY`, `STATE_AGENCY`, `MTA`, `OTHER_ENTITY`); `PROJECT_ID` ← `dot_project_id`; `PROJECT_TYPE` ← `type_of_project` |
| 5 | 3. Location | `BOROUGH`, `NORTH-SOUTH_STREET`, `EAST-WEST_STREET` ← the corner's; `CORNER_ID` ← the TIF's `dot_corner_id`, else the corner's; `RAMP_ID` ← the TIF's `dot_ramp_id`, else the ramp's `ramp_id_label`; `CORNER_DIRECTION` (text) ← the corner's direction spelled out (Southwest); `TINTERSECTION` ← `t_intersection` (/YES /NO); corner-type boxes `INTERSECTION CORNER`, `ISLANDMEDIAN`, `MIDBLOCKTOP OF T` (mid-block and top of "T" both tick the last); `RAMPCONSTRUCTION` ← `ramp_construction` |
| 5 | 4. Reasons | `LIMITED RIGHTOFWAY`, `EXISTING TOPOGRAPHY`, `EXISTING UTILITIES`, `STRUCTURES BUILDINGS VAULTS BRIDGES`, `TREES NATURAL FEATURES`, `NYCT  STRUCTURES ENTRANCES VENTS` (two spaces, DOT's), `HISTORIC FEATURES`, `OTHER_2` ← `reason_*`; `OTHER_EXPLANATION` ← `reason_other_text` |
| 5 | 5. Why | `EXPLANATION` ← `why_requirements_cannot_be_achieved` |
| 6 | 6. Elements | `RAMP WIDTH`, `RAMP RUNNING SLOPE`, `RAMP CROSS SLOPE`, `FLARE SLOPES`, `DETECTABLE WARNING SURFACE` (← `element_dws`), `LANDING DIMENSIONS`, `LANDING SLOPES`, `GUTTER CROSS SLOPE`, `GUTTER FLOW SLOPE`, `OTHER_3` ← `element_*`; `OTHER_NON-ADA_EXPLAIN` ← `element_other_text` |
| 6 | 7. Investigation | `INVESTIGATION_CONFIRMED` ← `investigation_confirmed`; `INVESTIGATION_EXPLANATION` ← `additional_explanation` |
| 7 | 8. Photos | `Photo_1-1`..`Photo_1-6` ← the TIF's photos, as the inspection form's (first six by number, captioned; more are logged) |
| 8 | 9. Design layout | `Design_Layout` ← the TIF's one `layout` image, if any (no caption) |
| 9 | 10. Statement | `ENGINEERSARCHITECTLANDSCAPE ARCHITECTS NAME HEREBY CERTIFY` and `NAME` ← `signer_name`; `STATE_LICENSE_NUM` ← `signer_ny_license_no`; `DATE` ← `signed_at`'s day in America/New_York (mm/dd/yyyy) |

Some of DOT's check boxes have only an "on" appearance (`RAMP CROSS SLOPE`, `INVESTIGATION_CONFIRMED`, the corner
types): unticked, they are set to `/Off`, which draws nothing.

### The signature: a picture, not a cryptographic signature

DOT's `SIGNATURE` is a cryptographic signature field (`/Sig`). ICID never sets it: it draws the RE's signature PNG
(letterboxed in the field's box) as page content under the field, so the page shows the signature and the field stays
unsigned and signable. **It is not a cryptographic signature.** If DOT requires one, the RE re-signs the field in
Acrobat (which is still possible, the field being empty).

### Never filled

Everything under **FOR INTERNAL USE ONLY** is DOT's: `APPROVED`, `DENIED`, `RESUBMIT SEE COMMENTS BELOW`,
`NYC DOTDDC REPRESENTATIVE`, `TITLE`, `SIGNATURE_2`, `DATE_2`, `COMMENTS`. The `SIGNATURE` field's value (above).

## Size and time

pypdf writes the template without its object streams, so an empty filled copy is about 2.05 MB (DOT's file is 1.7
MB); each photo adds about 170-300 KB. A copy with twelve photos is about 4 MB, so the files should go to Storage and be
handed out by signed URL, not returned in a function's response (Vercel refuses a response body over 4.5 MB). A 3-ramp
corner with twelve 4000 x 3000 photos fills in about 1.9 s on a developer machine (the photos' shrinking is most of it).
