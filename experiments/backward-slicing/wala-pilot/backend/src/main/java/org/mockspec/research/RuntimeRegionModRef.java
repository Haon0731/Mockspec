package org.mockspec.research;

import com.ibm.wala.classLoader.IField;
import com.ibm.wala.ipa.callgraph.propagation.InstanceKey;
import com.ibm.wala.ipa.callgraph.propagation.PointerAnalysis;
import com.ibm.wala.ipa.callgraph.propagation.PointerKey;
import com.ibm.wala.ipa.callgraph.propagation.AbstractFieldPointerKey;
import com.ibm.wala.ipa.callgraph.propagation.ConcreteTypeKey;
import com.ibm.wala.ipa.modref.DelegatingExtendedHeapModel;
import com.ibm.wala.ipa.modref.ExtendedHeapModel;
import com.ibm.wala.ipa.modref.ModRef;
import com.ibm.wala.ipa.slicer.HeapExclusions;
import com.ibm.wala.types.ClassLoaderReference;
import com.ibm.wala.types.TypeReference;

/**
 * Coarsens runtime heap locations for dependence analysis, preserving application/library fields.
 * Pointer analysis is unchanged. The merged location is not a StaticFieldKey: WALA 1.7.2's
 * HeapReachingDefs therefore uses weak updates instead of killing distinct merged static stores.
 * This is a precision trade-off, not a proof that runtime state has no influence.
 */
public final class RuntimeRegionModRef extends ModRef<InstanceKey> {
  /**
   * A non-static field key is required here. HeapExclusions and PDG only recognize field keys,
   * while HeapReachingDefs performs strong updates for StaticFieldKey. One stable instance keeps
   * the four runtime location forms in the same weakly-updated may-alias region.
   */
  static final class RuntimeRegion extends AbstractFieldPointerKey {
    RuntimeRegion(InstanceKey representative) {
      super(representative);
    }

    @Override public boolean equals(Object other) {
      return other instanceof RuntimeRegion;
    }

    @Override public int hashCode() {
      return RuntimeRegion.class.hashCode();
    }

    @Override public String toString() {
      return "unknown:primordial-runtime-region";
    }
  }

  @Override
  public ExtendedHeapModel makeHeapModel(PointerAnalysis<InstanceKey> analysis) {
    var objectClass = analysis.getClassHierarchy().lookupClass(TypeReference.JavaLangObject);
    if (objectClass == null) throw new IllegalStateException("java.lang.Object is absent from hierarchy");
    PointerKey runtimeRegion = new RuntimeRegion(new ConcreteTypeKey(objectClass));
    if (HeapExclusions.getType(runtimeRegion) == null) {
      throw new IllegalStateException("runtime region is invisible to WALA heap dependence");
    }
    return new DelegatingExtendedHeapModel(analysis.getHeapModel()) {
      private boolean runtime(InstanceKey instance) {
        return instance.getConcreteType().getClassLoader().getReference()
            .equals(ClassLoaderReference.Primordial);
      }

      private boolean runtime(IField field) {
        return field.getDeclaringClass().getClassLoader().getReference()
            .equals(ClassLoaderReference.Primordial);
      }

      @Override public PointerKey getPointerKeyForInstanceField(InstanceKey instance, IField field) {
        return runtime(field) ? runtimeRegion : super.getPointerKeyForInstanceField(instance, field);
      }

      @Override public PointerKey getPointerKeyForStaticField(IField field) {
        return runtime(field) ? runtimeRegion : super.getPointerKeyForStaticField(field);
      }

      @Override public PointerKey getPointerKeyForArrayContents(InstanceKey instance) {
        return runtime(instance) ? runtimeRegion : super.getPointerKeyForArrayContents(instance);
      }

      @Override public PointerKey getPointerKeyForArrayLength(InstanceKey instance) {
        return runtime(instance) ? runtimeRegion : super.getPointerKeyForArrayLength(instance);
      }
    };
  }
}
