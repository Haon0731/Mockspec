package com.google.cloud.tools.jib.cli.jar;
import com.google.cloud.tools.jib.api.JibContainerBuilder;
import com.google.cloud.tools.jib.api.buildplan.ContainerBuildPlan;
import com.google.cloud.tools.jib.cli.ArtifactProcessor;
import com.google.cloud.tools.jib.cli.CommonCliOptions;
import com.google.cloud.tools.jib.cli.CommonContainerConfigCliOptions;
import com.google.cloud.tools.jib.cli.Jar;
import com.google.cloud.tools.jib.plugins.common.logging.ConsoleLogger;
public final class JibDiagnosticEntry {
  public static ContainerBuildPlan observe(ArtifactProcessor processor, Jar jar,
      CommonCliOptions common, CommonContainerConfigCliOptions container, ConsoleLogger logger)
      throws Exception {
    JibContainerBuilder builder =
        JarFiles.toJibContainerBuilder(processor, jar, common, container, logger);
    return builder.toContainerBuildPlan();
  }
}
