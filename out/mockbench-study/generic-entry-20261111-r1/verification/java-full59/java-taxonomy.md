# Final Java generic-backend taxonomy

Run: `out/mockbench-study/backward-slice-generic165-20261111-r1`  
Input mode: `exact_terminal_java_fallback`  
Input digest: `af25b223fd4677f1f334bbee21acc73cdd720f61de084c72a083caf936d8f9a8`  
Cohort: still active; Java: 59 terminal cases.

This is a stage/evidence taxonomy. Backend completion and relation counts do not establish semantic correctness or complete slicing.

## Aggregate counts

- Top-level status: `{"failed": 41, "partial": 18}`
- Backend status: `{"complete": 18, "failed": 41}`
- Analysis entry: `{"not_started": 41, "observation_linked_to_product": 9, "observation_mapping_incomplete": 4, "observations_mapped_only": 5}`
- Observations: 61/250 mapped; 28 mapped observations record a statement frontier across 9 cases.
- Case stages: 58 input-prepared, 18 bytecode-compiled, 17 graph-created, 14 all-roots-mapped, 6 with call binding, 12 with typed influence.
- Relation kinds: `{"call_binding": 12, "value_flow": 2003}`
- Relations linked to frontier observations: `{"call_binding": 1, "value_flow": 1989}`
- Opaque boundaries: 111 across 11 cases; 72 typed paths suppressed.

## Concrete terminal failures

| Stage | Type | Count | Message | Examples |
| --- | --- | ---: | --- | --- |
| source_compilation | ValueError | 33 | deleted Java declaration types are not uniquely constrained by visible usage | com.ctrip.framework.apollo.configservice.filter.ClientAuthenticationFilterTest#testUnauthorized@6ff6ddd4, com.ctrip.framework.apollo.openapi.filter.ConsumerAuthenticationFilterTest#testAuthFailed@6ff6ddd4, com.google.cloud.tools.jib.api.JibContainerBuilderTest#testContainerize_executorCreated@6e35bb26, com.google.cloud.tools.jib.cli.jar.JarFilesTest#testToJibContainerBuilder_explodedStandard_basicInfo@69ae66df, io.grpc.xds.internal.sds.SdsProtocolNegotiatorsTest#clientSdsProtocolNegotiatorNewHandler_withTlsContextAttribute@5f9d000a |
| source_compilation | ValueError | 7 | masked Java input does not compile and no deleted variable is identifiable | de.codecentric.boot.admin.client.registration.CloudFoundryApplicationFactoryTest#should_use_application_uri@d0085edf, de.codecentric.boot.admin.client.registration.DefaultApplicationFactoryTest#test_all_baseUrls@d0085edf, org.apache.dubbo.rpc.filter.GenericFilterTest#testInvokeWithMethodArgumentSizeIsNot3@ebafc18c, org.apache.dubbo.rpc.filter.GenericFilterTest#testInvokeWithMethodNamtNot$Invoke@ebafc18c, org.apache.dubbo.rpc.filter.GenericImplFilterTest#testInvokeWith$Invoke@ebafc18c |
| admission | ValueError | 1 | Java source selection included a non-product support source | com.ctrip.framework.apollo.configservice.service.config.DefaultConfigServiceTest#testLoadConfigWithGrayRelease@67a78efb |

## Concrete observation-mapping gaps

| Reason | Observations | Cases | Examples |
| --- | ---: | ---: | --- |
| observation line does not contain one assertion invocation | 58 | 3 | org.apache.hadoop.fs.azurebfs.services.TestAbfsOutputStream#verifyShortWriteRequest@b612c310, org.apache.hadoop.fs.azurebfs.services.TestAbfsOutputStream#verifyWriteRequestOfBufferSize@35570e41, org.apache.hadoop.fs.azurebfs.services.TestAbfsOutputStream#verifyWriteRequestOfBufferSizeWithAppendBlob@c1fde4fe |
| WALA ArrayClass.getAllInstanceFields UnimplementedError during opaque summaries | 4 | 1 | org.apache.dubbo.qos.command.impl.LsTest#testExecute@f186f2bc |
| ambiguous_or_package_mismatched_visible_type | 2 | 1 | org.apache.hadoop.fs.azurebfs.services.TestAbfsOutputStream#verifyWriteRequestOfBufferSize@35570e41 |

## Opaque-boundary precision

Known placeholder spans suppress typed paths and remain unknown. Detection is limited to narrow visible single-throw syntax. Keyword-like detected names require source review; affected cases: 5.

## Per-case root mapping and evidence

| Case | Backend/stage | Roots | Entry status | Relations | Frontier roots | Opaque/suppressed | Error |
| --- | --- | ---: | --- | --- | ---: | ---: | --- |
| com.ctrip.framework.apollo.configservice.controller.ConfigFileControllerTest#testQueryConfigAsJson@67a78efb | complete/mapped | 4/4 | observation_linked_to_product | {"call_binding": 2, "value_flow": 49} | 3 | 4/0 |  |
| com.ctrip.framework.apollo.configservice.controller.ConfigFileControllerTest#testQueryConfigAsProperties@67a78efb | complete/mapped | 10/10 | observation_linked_to_product | {"call_binding": 4, "value_flow": 4} | 0 | 4/0 |  |
| com.ctrip.framework.apollo.configservice.controller.ConfigFileControllerTest#testQueryConfigWithGrayRelease@67a78efb | complete/mapped | 4/4 | observation_linked_to_product | {"call_binding": 2, "value_flow": 20} | 1 | 3/0 |  |
| com.ctrip.framework.apollo.configservice.filter.ClientAuthenticationFilterTest#testAuthorizedSuccessfully@6ff6ddd4 | complete/mapped | 2/2 | observation_linked_to_product | {"value_flow": 8} | 2 | 3/0 |  |
| com.ctrip.framework.apollo.configservice.filter.ClientAuthenticationFilterTest#testUnauthorized@6ff6ddd4 | failed/source_compilation | 0/2 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| com.ctrip.framework.apollo.configservice.service.config.DefaultConfigServiceTest#testLoadConfigWithGrayRelease@67a78efb | failed/admission | 0/1 | not_started | {} | 0 | 0/0 | Java source selection included a non-product support source |
| com.ctrip.framework.apollo.openapi.filter.ConsumerAuthenticationFilterTest#testAuthFailed@6ff6ddd4 | failed/source_compilation | 0/2 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| com.ctrip.framework.apollo.openapi.filter.ConsumerAuthenticationFilterTest#testAuthSuccessfully@6ff6ddd4 | complete/mapped | 2/2 | observations_mapped_only | {} | 2 | 3/0 |  |
| com.google.cloud.tools.jib.api.JibContainerBuilderTest#testContainerize_executorCreated@6e35bb26 | failed/source_compilation | 0/1 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| com.google.cloud.tools.jib.cli.jar.JarFilesTest#testToJibContainerBuilder_explodedStandard_basicInfo@69ae66df | failed/source_compilation | 0/19 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| com.netflix.netty.common.CloseOnIdleStateHandlerTest#incrementCounterOnIdleStateEvent@9f103844 | complete/mapped | 2/2 | observations_mapped_only | {} | 0 | 0/0 |  |
| de.codecentric.boot.admin.client.registration.CloudFoundryApplicationFactoryTest#should_use_application_uri@d0085edf | failed/source_compilation | 0/5 | not_started | {} | 0 | 0/0 | masked Java input does not compile and no deleted variable is identifiable |
| de.codecentric.boot.admin.client.registration.DefaultApplicationFactoryTest#test_all_baseUrls@d0085edf | failed/source_compilation | 0/5 | not_started | {} | 0 | 0/0 | masked Java input does not compile and no deleted variable is identifiable |
| io.grpc.internal.ManagedChannelImplBuilderTest#getDefaultPort_custom@ef784649 | complete/mapped | 2/2 | observation_linked_to_product | {"call_binding": 2, "value_flow": 4} | 0 | 8/0 |  |
| io.grpc.xds.internal.sds.SdsProtocolNegotiatorsTest#clientSdsProtocolNegotiatorNewHandler_withTlsContextAttribute@5f9d000a | failed/source_compilation | 0/3 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| io.grpc.xds.internal.sds.SdsProtocolNegotiatorsTest#clientSdsProtocolNegotiatorNewHandler_withTlsContextAttribute@9016cf55 | failed/source_compilation | 0/3 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| org.apache.druid.data.input.avro.SchemaRegistryBasedAvroBytesDecoderTest#testParse@99198c02 | failed/source_compilation | 0/4 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| org.apache.druid.server.coordinator.duty.KillCompactionConfigTest#testRunDoNothingIfCurrentConfigIsEmpty@e5633d78 | failed/source_compilation | 0/1 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| org.apache.dubbo.qos.command.impl.LsTest#testExecute@f186f2bc | complete/mapped | 0/4 | observation_mapping_incomplete | {} | 0 | 0/0 |  |
| org.apache.dubbo.registry.client.migration.DefaultMigrationAddressComparatorTest#test@2bca144b | failed/source_compilation | 0/6 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| org.apache.dubbo.rpc.filter.ExceptionFilterTest#testJavaException@ebafc18c | failed/source_compilation | 0/3 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| org.apache.dubbo.rpc.filter.ExceptionFilterTest#testRuntimeException@ebafc18c | failed/source_compilation | 0/3 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| org.apache.dubbo.rpc.filter.ExecuteLimitFilterTest#testExecuteLimitInvoke@ebafc18c | failed/source_compilation | 0/2 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| org.apache.dubbo.rpc.filter.GenericFilterTest#testInvokeWithDefault@ebafc18c | failed/source_compilation | 0/3 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| org.apache.dubbo.rpc.filter.GenericFilterTest#testInvokeWithMethodArgumentSizeIsNot3@ebafc18c | failed/source_compilation | 0/3 | not_started | {} | 0 | 0/0 | masked Java input does not compile and no deleted variable is identifiable |
| org.apache.dubbo.rpc.filter.GenericFilterTest#testInvokeWithMethodNamtNot$Invoke@ebafc18c | failed/source_compilation | 0/3 | not_started | {} | 0 | 0/0 | masked Java input does not compile and no deleted variable is identifiable |
| org.apache.dubbo.rpc.filter.GenericImplFilterTest#testInvoke@ebafc18c | failed/source_compilation | 0/3 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| org.apache.dubbo.rpc.filter.GenericImplFilterTest#testInvokeWith$Invoke@ebafc18c | failed/source_compilation | 0/2 | not_started | {} | 0 | 0/0 | masked Java input does not compile and no deleted variable is identifiable |
| org.apache.dubbo.rpc.filter.GenericImplFilterTest#testInvokeWithException@ebafc18c | failed/source_compilation | 0/2 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| org.apache.dubbo.rpc.filter.TokenFilterTest#testInvokeWithToken@ebafc18c | failed/source_compilation | 0/2 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| org.apache.dubbo.rpc.filter.TokenFilterTest#testInvokeWithWrongToken@c783e55e | failed/source_compilation | 0/2 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| org.apache.hadoop.fs.azurebfs.services.TestAbfsOutputStream#verifyShortWriteRequest@b612c310 | complete/mapped | 6/24 | observation_mapping_incomplete | {"value_flow": 534} | 6 | 20/36 |  |
| org.apache.hadoop.fs.azurebfs.services.TestAbfsOutputStream#verifyWriteRequest@b612c310 | complete/mapped | 4/4 | observations_mapped_only | {} | 0 | 20/0 |  |
| org.apache.hadoop.fs.azurebfs.services.TestAbfsOutputStream#verifyWriteRequestOfBufferSize@35570e41 | complete/mapped | 6/34 | observation_mapping_incomplete | {"value_flow": 738} | 6 | 22/18 |  |
| org.apache.hadoop.fs.azurebfs.services.TestAbfsOutputStream#verifyWriteRequestOfBufferSizeWithAppendBlob@c1fde4fe | complete/mapped | 6/18 | observation_mapping_incomplete | {"value_flow": 642} | 6 | 20/18 |  |
| org.apache.hadoop.mapreduce.TestJob#testJobToString@6e11461e | failed/source_compilation | 0/7 | not_started | {} | 0 | 0/0 | masked Java input does not compile and no deleted variable is identifiable |
| org.apache.ibatis.type.LocalDateTimeTypeHandlerTest#shouldGetResultFromCallableStatement@963a8a57 | failed/source_compilation | 0/1 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| org.apache.ibatis.type.LocalDateTimeTypeHandlerTest#shouldGetResultFromResultSetByName@963a8a57 | failed/source_compilation | 0/1 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| org.apache.ibatis.type.LocalDateTimeTypeHandlerTest#shouldGetResultFromResultSetByPosition@963a8a57 | failed/source_compilation | 0/1 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| org.apache.ibatis.type.LocalDateTypeHandlerTest#shouldGetResultFromCallableStatement@963a8a57 | failed/source_compilation | 0/1 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| org.apache.ibatis.type.LocalDateTypeHandlerTest#shouldGetResultFromResultSetByName@963a8a57 | failed/source_compilation | 0/1 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| org.apache.ibatis.type.LocalDateTypeHandlerTest#shouldGetResultFromResultSetByPosition@963a8a57 | failed/source_compilation | 0/1 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| org.apache.ibatis.type.LocalTimeTypeHandlerTest#shouldGetResultFromResultSetByName@963a8a57 | failed/source_compilation | 0/1 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| org.apache.ibatis.type.OffsetDateTimeTypeHandlerTest#shouldGetResultFromCallableStatement@c7b1d95e | failed/source_compilation | 0/1 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| org.apache.ibatis.type.OffsetDateTimeTypeHandlerTest#shouldGetResultFromResultSetByName@c7b1d95e | failed/source_compilation | 0/1 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| org.apache.ibatis.type.OffsetDateTimeTypeHandlerTest#shouldGetResultFromResultSetByPosition@c7b1d95e | failed/source_compilation | 0/1 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| org.apache.ibatis.type.OffsetTimeTypeHandlerTest#shouldGetResultFromCallableStatement@f5124674 | failed/source_compilation | 0/1 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| org.apache.ibatis.type.OffsetTimeTypeHandlerTest#shouldGetResultFromResultSetByPosition@f5124674 | failed/source_compilation | 0/1 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| org.apache.zookeeper.server.FinalRequestProcessorTest#testACLDigestHashHiding_NoAuth_WorldCanRead@777f0c15 | failed/source_compilation | 0/20 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| org.neo4j.harness.junit.rule.Neo4jRuleTest#shouldReturnHttpsUriWhenConfigured@9781c3cc | failed/source_compilation | 0/3 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| org.neo4j.internal.recordstorage.NeoStoreTransactionApplierTest#shouldApplyPropertyKeyTokenCommandToTheStoreInRecovery@2a4b1ef5 | failed/source_compilation | 0/1 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| org.neo4j.kernel.impl.newapi.OperationsTest#shouldAcquireTxStateBeforeAllocatingSchemaId@4ec55bf8 | failed/source_compilation | 0/2 | not_started | {} | 0 | 0/0 | masked Java input does not compile and no deleted variable is identifiable |
| org.neo4j.server.http.cypher.InvocationTest#shouldHandleCypherSyntaxError@a3bfc551 | complete/mapped | 2/2 | observations_mapped_only | {} | 0 | 4/0 |  |
| org.springframework.boot.actuate.availability.ReadinessProbeHealthIndicatorTests#readinessIsReady@bb79c847 | failed/source_compilation | 0/2 | not_started | {} | 0 | 0/0 | deleted Java declaration types are not uniquely constrained by visible usage |
| zipkin2.storage.cassandra.SchemaTest#ensureKeyspaceMetadata_failsWhenKeyspaceMetadataIsNotNull@9674c46f | complete/mapped | 3/3 | observations_mapped_only | {} | 0 | 0/0 |  |
| zipkin2.storage.cassandra.SchemaTest#getKeyspaceMetadata_failsWhenOneVersionLessThan3_11_3@9674c46f | complete/mapped | 2/2 | observation_linked_to_product | {"value_flow": 1} | 1 | 0/0 |  |
| zipkin2.storage.cassandra.SchemaTest#getKeyspaceMetadata_failsWhenVersionLessThan3_11_3@9674c46f | complete/mapped | 2/2 | observation_linked_to_product | {"value_flow": 1} | 1 | 0/0 |  |
| zipkin2.storage.cassandra.SchemaTest#getKeyspaceMetadata_passesWhenVersion3_11_3AndKeyspaceMetadataIsNotNull@9674c46f | complete/mapped | 2/2 | observation_linked_to_product | {"call_binding": 1, "value_flow": 1} | 0 | 0/0 |  |
| zipkin2.storage.cassandra.SchemaTest#getKeyspaceMetadata_passesWhenVersion3_11_4AndKeyspaceMetadataIsNotNull@9674c46f | complete/mapped | 2/2 | observation_linked_to_product | {"call_binding": 1, "value_flow": 1} | 0 | 0/0 |  |

Semantic claim boundary: All new relations remain analysis evidence unless their contract says otherwise; counts do not establish semantic correctness or complete slicing.
