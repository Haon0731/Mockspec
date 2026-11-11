package com.google.cloud.tools.jib.cli;
import com.google.cloud.tools.jib.api.buildplan.FileEntriesLayer;
import com.google.common.collect.ImmutableList;
import java.io.IOException;
import java.util.List;
public interface ArtifactProcessor {
  List<FileEntriesLayer> createLayers() throws IOException;
  ImmutableList<String> computeEntrypoint(List<String> flags) throws IOException;
  Integer getJavaVersion();
}
