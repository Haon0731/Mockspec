package com.google.cloud.tools.jib.cli;
import com.google.cloud.tools.jib.api.CredentialRetriever;
import com.google.cloud.tools.jib.plugins.common.DefaultCredentialRetrievers;
import java.util.List;
public final class Credentials {
  public static native List<CredentialRetriever> getFromCredentialRetrievers(
      CommonCliOptions options, DefaultCredentialRetrievers retrievers);
}
