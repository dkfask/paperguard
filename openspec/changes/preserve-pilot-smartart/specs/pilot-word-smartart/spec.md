## ADDED Requirements

### Requirement: 保留学校样例的内嵌 SmartArt

系统 SHALL 通过现有 Word 导入流程识别学校样例类的内嵌 SmartArt，对账后保留图形内容、显示比例及题注。

#### Scenario: 导入学校图 4.1

- **WHEN** 学生上传包含学校样例类自包含 SmartArt 的 Word 并确认通过对账的候选
- **THEN** 项目保留该图形和题注，重新打开或编辑题注时图形内容仍然完整

#### Scenario: 无法对账的 SmartArt

- **WHEN** 导入图形的内部部件缺失或引用无法核验
- **THEN** 导入对账指出无法保留的对象，阻止把不完整候选应用到稿件

### Requirement: 核验正式成品的字号和内容

系统 SHALL 从最终 DOCX 回读 SmartArt 及题注，核对图形内容、尺寸以及图内字号是否超过图题。

#### Scenario: 合规图形进入正式交付

- **WHEN** 成品完整保留已确认的图形且图内字号不超过对应图题
- **THEN** SmartArt 检查通过，其他正式交付条件仍由现有门禁判定

#### Scenario: 标注超限或对象丢失

- **WHEN** 成品缺少已确认图形，或标注字号超限或证据不足
- **THEN** 成品检查阻断交付

#### Scenario: 正文包含普通截图

- **WHEN** 真实规范要求核验，但截图缺少可信的图内标注字号证据
- **THEN** 当前要求仍标为待核验，现有硬性规则继续生效
