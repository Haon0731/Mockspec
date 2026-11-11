package com.google.cloud.tools.jib.cli;
import com.google.cloud.tools.jib.api.buildplan.AbsoluteUnixPath;
import com.google.cloud.tools.jib.api.buildplan.ImageFormat;
import com.google.cloud.tools.jib.api.buildplan.Port;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
public class CommonContainerConfigCliOptions {
  public native Optional<String> getFrom();
  public native Set<Port> getExposedPorts();
  public native Set<AbsoluteUnixPath> getVolumes();
  public native Map<String,String> getEnvironment();
  public native Map<String,String> getLabels();
  public native Optional<String> getUser();
  public native Optional<ImageFormat> getFormat();
  public native List<String> getProgramArguments();
  public native List<String> getEntrypoint();
  public native Optional<Instant> getCreationTime();
}
