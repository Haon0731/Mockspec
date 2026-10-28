# Synthetic Scenario Contract examples

`java.synthetic.json` and `python.synthetic.json` are hand-authored **synthetic
responses** used to exercise the Scenario Contract schema and evidence validator.
They are not model output and do not demonstrate that live model inference is
available or semantically correct.

The examples cover the two cases already approved in
`src/mock_spec/reviewed_inputs.json`. Their evidence was derived only by loading
the reviewed frozen snapshots through the existing Java and Python adapters and
then reading the resulting `UnifiedTask` sources by `source_id`. No reference
mock, ground truth, historical score, raw dataset, or archive candidate was read.

The local frozen snapshot roots used for this checked example are:

- Java: `/home/haon/projects/Stub-Tester/.omx/developer-realistic-registry-max/formal-snapshots/java-bechmark-strong/5b93995c794af0152854a01ddda2fe77948b5ab828b05282b00728f7729f64d4`
- Python: `/home/haon/projects/Stub-Tester/.omx/developer-realistic-registry-max-fix23/formal-snapshots/python-benchmark-new/241ef457f8d071c23e6acd658b684b11b54f13fa955e097d6bebe35dc755f47d`

The Java response keeps `getJavaVersion() <= 8` conditional on the absent
`getFrom()` path and keeps `computeEntrypoint()` conditional on an empty custom
entrypoint. The Python response preserves both ways execution can leave the app
aborter call: an exception stops execution, while a normal return falls through
to Werkzeug abort. `expected_interactions` describe control flow and all carry
`assertion_policy: "not_implied"`; they do not automatically prescribe mock
assertions.

These contracts establish structural validity and exact evidence locations only.
They do not prove that a scenario is the unique or semantically correct test
setup.

Replay the synthetic responses through the same adapter and validator pipeline:

```bash
mock-spec-scenario \
  --language java \
  --snapshot /home/haon/projects/Stub-Tester/.omx/developer-realistic-registry-max/formal-snapshots/java-bechmark-strong/5b93995c794af0152854a01ddda2fe77948b5ab828b05282b00728f7729f64d4 \
  --case-id 'com.google.cloud.tools.jib.cli.jar.JarFilesTest#testToJibContainerBuilder_explodedStandard_basicInfo@69ae66df' \
  --response-json examples/scenarios/java.synthetic.json \
  --output examples/scenarios/java.replay.json

mock-spec-scenario \
  --language python \
  --snapshot /home/haon/projects/Stub-Tester/.omx/developer-realistic-registry-max-fix23/formal-snapshots/python-benchmark-new/241ef457f8d071c23e6acd658b684b11b54f13fa955e097d6bebe35dc755f47d \
  --case-id flask_308 \
  --response-json examples/scenarios/python.synthetic.json \
  --output examples/scenarios/python.replay.json
```

## 首轮 Spec 语义校准

[六类微型合成参考卡及 flask_308 适配草案](semantic-calibration/README.md)补充语义评阅、
正式字段投影与离线复现。均为手写参考草案，不是模型结果或人工金标准。
旧 synthetic/replay 文件保留原用途，不改写历史回放。

## 自包含消费协议

[版本化依赖规格示例](consumer-specs/README.md)展示关系允许多个合法 Mock，以及关键未知如何限制消费。
这些文件由人工合成假响应经过当前研究控制器生成；不是实际模型推断结果。
