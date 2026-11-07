# Data Sources

Licences, terms and access rules for every PathSense source. Dataset IDs and polling cadences are in [PLAN.md section 6](../PLAN.md#6-data-sources). Each source owner adds their section here.

## Cameras and training data (OP-112)

Checked on November 7, 2026, by Ipsa. Open questions are marked **Open** and tracked in issue #40.

### 511 Alberta cameras

| Item | Finding |
| --- | --- |
| Access | Official API with a free developer key: register an account, log in, request a key on the API documentation page. Key stored as `ALBERTA_511_KEY`, never committed. |
| Endpoint | `GET https://511.alberta.ca/api/v2/get/cameras?key=…&format=json`. Each camera has `Id`, `Source`, `SourceId`, `Roadway`, `Direction`, `Latitude`, `Longitude`, `Location`, `SortOrder` and `Views` (`Id`, `Url`, `Status`, `Description`). |
| Rate limit | 10 calls every 60 seconds. `vision/cameras.py` enforces it with one shared `RateLimiter`. |
| Terms of use | Information on 511 Alberta may be reproduced for non-commercial or educational purposes without charge or further permission. Commercial reproduction needs written permission from the Government of Alberta. Information is provided as is, without warranty. |
| Camera images | **Open:** the published terms do not mention camera images specifically; the non-commercial reproduction clause covers "information" on the site. We have asked 511 Alberta to confirm in writing that keeping labelled frames for non-commercial model training is allowed. Meanwhile, live frames are processed in memory only, and the app links to the official 511 viewer instead of re-hosting images. |
| Attribution | "Road camera data: 511 Alberta, Government of Alberta" on `/sources` and next to each camera reading. |
| Commercial use | Any use beyond the hackathon needs written permission from the Government of Alberta. |

Sources: [511 Alberta API documentation](https://511.alberta.ca/developers/doc), [GET Cameras](https://511.alberta.ca/help/endpoint/cameras), [About 511 Alberta (terms of use)](https://511.alberta.ca/about/about).

### City of Edmonton traffic cameras

| Item | Finding |
| --- | --- |
| Status | **Open:** written request to the City not yet answered. Date sent and replies are tracked in issue #40. |
| Until permission | Not used. The app links to the City's own viewer ([edmontontrafficcam.com](https://edmontontrafficcam.com/)). |

### RSCD (Road Surface Classification Dataset)

| Item | Finding |
| --- | --- |
| Licence | CC BY 4.0 on both IEEE DataPort and Mendeley Data. Training a model on it is allowed with attribution. |
| Attribution | Tong Zhao, "RSCD: Road Surface Classification Dataset with Detailed Annotations for Driving Assistance", IEEE DataPort, 2022, doi:10.21227/446p-xr65. Credit it in `vision/MODEL_CARD.md` and on `/sources`. |
| Size | The IEEE DataPort page describes about 1 million images; the Mendeley Data release (version 3) lists 370,151. Both are 240 x 360 pixel road-surface patches. |
| How images were taken | Cropped from a vehicle-mounted camera looking at the road just ahead, in China. Our cameras are roadside and look down a road from a distance, so RSCD is a pre-training aid, not a substitute for labelled 511 images. |
| Classes | 27 categories of friction, material and unevenness. **Open:** confirm which categories map to our `snow_partial`, `snow_covered` and `ice` before training (OP-202). |
| Access | **Open:** IEEE DataPort needs a subscription; download from the Mendeley Data release or the authors' GitHub instead. |

Sources: [RSCD on IEEE DataPort](https://ieee-dataport.org/documents/rscd-road-surface-classification-dataset-detailed-annotations-driving-assistance), [Road Surface Image Dataset on Mendeley Data](https://data.mendeley.com/datasets/w86hvkrzc5).

### Decision D-3 (training data), proposed

- Use RSCD (CC BY 4.0) for pre-training, with attribution.
- Keep labelled 511 frames for non-commercial training under the 511 non-commercial reproduction terms, stored privately outside the repo and never redistributed, and ask 511 Alberta to confirm in writing. If 511 Alberta says no, delete the stored frames and train on RSCD plus community photos only.
- Never keep an image in which a person can be identified, and never commit images to the repo.

The lead records the final decision in PLAN.md section 16.
