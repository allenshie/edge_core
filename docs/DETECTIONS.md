## EdgeDetection 輸出格式

`EdgeDetection` 是 edge 推理結果的共通格式，確保不同模型輸出可被同一套
pipeline 與 integration 端接收。常用欄位如下：

- `class_name`：物件類別名稱
- `score`：信心分數
- `bbox`：`[x1, y1, x2, y2]` 的像素座標
- `track_id`：可為 `None`，若模型未提供追蹤 id 則留空
- `polygon`：分割輪廓（可為 `None`）
- `keypoints`：姿態節點（可為 `None`）
- `category`：自定義分類字串（預設 `""`）
- `extra`：擴充欄位（`dict`）

### 建議用法

1) segmentation：使用 `polygon`，避免傳遞 `mask` 陣列造成過大負擔  
2) pose：使用 `keypoints`  
3) 自訂資訊：放在 `category` 或 `extra`

### 範例

```python
from edge.schema import EdgeDetection

det = EdgeDetection(
    track_id=None,
    class_name="person",
    score=0.92,
    bbox=[10, 20, 100, 200],
    polygon=[[10.0, 20.0], [100.0, 20.0], [100.0, 200.0], [10.0, 200.0]],
    keypoints=None,
    category="worker",
    extra={"helmet": True, "vest": False},
)
```

> 注意：若下游需要跨幀追蹤，建議提供 `track_id`；否則每幀會被當成新物件。
