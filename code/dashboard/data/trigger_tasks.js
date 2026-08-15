/* 由 pipeline/trigger/service.py 產生，請勿手動編輯。 */

window.TRIGGER_TASKS = [
 {
  "taskId": "QK-0001",
  "triggerType": "quake",
  "createdAt": "2026-08-14T15:36:22.817578",
  "aoi": {
   "centerLat": 23.72,
   "centerLon": 121.32,
   "radiusKm": 30.0,
   "method": "circle_buffer",
   "label": "震央 30km"
  },
  "basis": {
   "magnitude": 7.2,
   "epicenter": [
    23.72,
    121.32
   ],
   "sourceId": "DEMO-NOT-REAL",
   "reasons": [
    "規模 7.2 ≥ 5.5",
    "山區測站達 5弱 以上：萬榮（示範）、光復（示範）"
   ]
  },
  "nearbyKnownLakes": [
   {
    "id": "bl071",
    "name": "花蓮馬太鞍溪",
    "distanceKm": 3.1,
    "statusKey": "watch"
   },
   {
    "id": "bl057",
    "name": "萬里溪",
    "distanceKm": 5.8,
    "statusKey": "gone"
   },
   {
    "id": "bl065",
    "name": "花蓮萬里溪",
    "distanceKm": 7.0,
    "statusKey": "gone"
   },
   {
    "id": "bl075",
    "name": "花蓮萬里溪",
    "distanceKm": 9.5,
    "statusKey": "watch"
   },
   {
    "id": "bl049",
    "name": "郡大溪(丹大溪)",
    "distanceKm": 21.2,
    "statusKey": "gone"
   },
   {
    "id": "bl063",
    "name": "花蓮豐坪溪上游",
    "distanceKm": 25.7,
    "statusKey": "gone"
   },
   {
    "id": "bl002",
    "name": "丹大溪",
    "distanceKm": 26.2,
    "statusKey": "gone"
   }
  ],
  "priority": "high",
  "dispatch": {
   "latestScene": null,
   "nextScene": {
    "estimated": true,
    "next_pass_eta": "2026-08-20T15:36:22.817578",
    "basis": "經驗重訪週期 6.0 天外推，非真實軌道預報"
   },
   "queriedAt": "2026-08-14T15:36:22.817990",
   "hasCdseCredentials": false
  }
 }
];
