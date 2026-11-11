package com.google.cloud.tools.jib.cli.jar;
import com.google.cloud.tools.jib.api.JibContainerBuilder;
import com.google.cloud.tools.jib.api.buildplan.ContainerBuildPlan;
import com.google.cloud.tools.jib.api.buildplan.FileEntriesLayer;
import com.google.cloud.tools.jib.cli.ArtifactProcessor;
import com.google.cloud.tools.jib.cli.CommonCliOptions;
import com.google.cloud.tools.jib.cli.CommonContainerConfigCliOptions;
import com.google.cloud.tools.jib.cli.Jar;
import com.google.cloud.tools.jib.plugins.common.logging.ConsoleLogger;
public final class JibObservationEntry {
  public static Object[] observe(ArtifactProcessor processor, Jar jar,
      CommonCliOptions common, CommonContainerConfigCliOptions container, ConsoleLogger logger)
      throws Exception {
    JibContainerBuilder builder =
        JarFiles.toJibContainerBuilder(processor, jar, common, container, logger);
    ContainerBuildPlan plan = builder.toContainerBuildPlan();
    ObservationSink.baseImage(plan.getBaseImage());
    ObservationSink.platforms(plan.getPlatforms());
    ObservationSink.creationTime(plan.getCreationTime());
    ObservationSink.format(plan.getFormat());
    ObservationSink.environmentSize(plan.getEnvironment().size());
    ObservationSink.labelsSize(plan.getLabels().size());
    ObservationSink.volumesSize(plan.getVolumes().size());
    ObservationSink.exposedPortsSize(plan.getExposedPorts().size());
    ObservationSink.user(plan.getUser());
    ObservationSink.workingDirectory(plan.getWorkingDirectory());
    ObservationSink.entrypointSize(plan.getEntrypoint().size());
    ObservationSink.entrypoint0(plan.getEntrypoint().get(0));
    ObservationSink.entrypoint1(plan.getEntrypoint().get(1));
    ObservationSink.entrypoint2(plan.getEntrypoint().get(2));
    ObservationSink.entrypoint3(plan.getEntrypoint().get(3));
    ObservationSink.layersSize(plan.getLayers().size());
    ObservationSink.layer0Name(plan.getLayers().get(0).getName());
    ObservationSink.layer0Entries(
        ((FileEntriesLayer) plan.getLayers().get(0)).getEntries().toArray(new Object[0]));
    ObservationSink.layer0Entry0ExtractionPath(
        ((FileEntriesLayer) plan.getLayers().get(0)).getEntries().get(0).getExtractionPath());
    return new Object[] {plan};
  }
}
