/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

// Command plan renders the full set of resources the operator would
// apply for a given WitwaveAgent spec, without touching a cluster (#1111).
//
// Usage:
//
//	operator-plan -f agent.yaml
//	cat agent.yaml | operator-plan
//
// The input is one or more YAML documents containing WitwaveAgent CRs. For
// each document, plan emits the rendered Deployment, Service,
// ConfigMaps, PVCs, HPA, PDB, dashboard resources, and manifest-team
// ConfigMap (if the agent is a solo team) as a single multi-document
// YAML stream on stdout. Resources whose build depends on live-cluster
// state (team-peer manifests beyond self, prompt-bound ConfigMaps) are
// rendered with empty peer-sets / prompt-sets so the output represents
// the "no other CRs in namespace" baseline — enough to validate a new
// CR's shape before commit.
//
// Exit codes:
//
//	0 — rendered successfully
//	1 — input parse error or render failure
package main

import (
	"flag"
	"fmt"
	"io"
	"os"

	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/util/yaml"
	clientgoscheme "k8s.io/client-go/kubernetes/scheme"
	sigyaml "sigs.k8s.io/yaml"

	witwavev1alpha1 "github.com/witwave-ai/witwave-operator/api/v1alpha1"
	"github.com/witwave-ai/witwave-operator/internal/controller"
)

func main() {
	var inputPath string
	flag.StringVar(&inputPath, "f", "", "Path to a YAML file containing one or more WitwaveAgent documents. Reads stdin when unset.")
	flag.Parse()

	var input io.Reader = os.Stdin
	if inputPath != "" {
		f, err := os.Open(inputPath)
		if err != nil {
			fmt.Fprintf(os.Stderr, "open %s: %v\n", inputPath, err)
			os.Exit(1)
		}
		defer f.Close()
		input = f
	}

	scheme := runtime.NewScheme()
	if err := clientgoscheme.AddToScheme(scheme); err != nil {
		fmt.Fprintf(os.Stderr, "register core scheme: %v\n", err)
		os.Exit(1)
	}
	if err := witwavev1alpha1.AddToScheme(scheme); err != nil {
		fmt.Fprintf(os.Stderr, "register witwave scheme: %v\n", err)
		os.Exit(1)
	}

	dec := yaml.NewYAMLOrJSONDecoder(input, 4096)
	docIdx := 0
	for {
		doc := map[string]interface{}{}
		if err := dec.Decode(&doc); err != nil {
			if err == io.EOF {
				break
			}
			fmt.Fprintf(os.Stderr, "decode document %d: %v\n", docIdx, err)
			os.Exit(1)
		}
		if len(doc) == 0 {
			continue
		}
		docIdx++

		raw, err := sigyaml.Marshal(doc)
		if err != nil {
			fmt.Fprintf(os.Stderr, "re-marshal document %d: %v\n", docIdx, err)
			os.Exit(1)
		}
		agent := &witwavev1alpha1.WitwaveAgent{}
		if err := sigyaml.Unmarshal(raw, agent); err != nil {
			fmt.Fprintf(os.Stderr, "parse WitwaveAgent document %d: %v\n", docIdx, err)
			os.Exit(1)
		}
		if agent.Kind != "" && agent.Kind != "WitwaveAgent" {
			fmt.Fprintf(os.Stderr, "skipping document %d: kind=%q is not WitwaveAgent\n", docIdx, agent.Kind)
			continue
		}
		if agent.Namespace == "" {
			agent.Namespace = "default"
		}

		if err := renderAgent(agent, os.Stdout); err != nil {
			fmt.Fprintf(os.Stderr, "render agent %s/%s: %v\n", agent.Namespace, agent.Name, err)
			os.Exit(1)
		}
	}
}

// renderAgent writes every resource controller.buildXxx would produce
// for “agent“ as a multi-document YAML stream. Resource ordering
// mirrors Reconcile.
func renderAgent(agent *witwavev1alpha1.WitwaveAgent, w io.Writer) error {
	emit := func(kind, name string, obj interface{}) error {
		if obj == nil {
			return nil
		}
		out, err := sigyaml.Marshal(obj)
		if err != nil {
			return fmt.Errorf("marshal %s/%s: %w", kind, name, err)
		}
		if _, err := fmt.Fprintf(w, "---\n# %s %s/%s (agent=%s)\n", kind, agent.Namespace, name, agent.Name); err != nil {
			return err
		}
		if _, err := w.Write(out); err != nil {
			return err
		}
		return nil
	}

	if _, err := fmt.Fprintf(w, "# === plan for WitwaveAgent %s/%s ===\n", agent.Namespace, agent.Name); err != nil {
		return err
	}

	if dep := controller.BuildDeploymentForPlan(agent); dep != nil {
		if err := emit("Deployment", dep.Name, dep); err != nil {
			return err
		}
	}
	if svc := controller.BuildServiceForPlan(agent); svc != nil {
		if err := emit("Service", svc.Name, svc); err != nil {
			return err
		}
	}
	for _, cm := range controller.BuildConfigMapsForPlan(agent) {
		if err := emit("ConfigMap", cm.Name, cm); err != nil {
			return err
		}
	}
	pvcs, pvcErrs := controller.BuildBackendPVCsForPlan(agent)
	for _, pvc := range pvcs {
		if err := emit("PersistentVolumeClaim", pvc.Name, pvc); err != nil {
			return err
		}
	}
	for _, pvcErr := range pvcErrs {
		fmt.Fprintf(os.Stderr, "[warn] PVC build skipped: %v\n", pvcErr)
	}
	if sharedPVC, err := controller.BuildSharedStoragePVCForPlan(agent); err != nil {
		fmt.Fprintf(os.Stderr, "[warn] shared PVC build skipped: %v\n", err)
	} else if sharedPVC != nil {
		if err := emit("PersistentVolumeClaim", sharedPVC.Name, sharedPVC); err != nil {
			return err
		}
	}
	if hpa := controller.BuildHPAForPlan(agent); hpa != nil {
		if err := emit("HorizontalPodAutoscaler", hpa.Name, hpa); err != nil {
			return err
		}
	}
	if pdb := controller.BuildPDBForPlan(agent); pdb != nil {
		if err := emit("PodDisruptionBudget", pdb.Name, pdb); err != nil {
			return err
		}
	}
	if dcm := controller.BuildDashboardConfigMapForPlan(agent); dcm != nil {
		if err := emit("ConfigMap", dcm.Name, dcm); err != nil {
			return err
		}
	}
	if ddep := controller.BuildDashboardDeploymentForPlan(agent); ddep != nil {
		if err := emit("Deployment", ddep.Name, ddep); err != nil {
			return err
		}
	}
	if dsvc := controller.BuildDashboardServiceForPlan(agent); dsvc != nil {
		if err := emit("Service", dsvc.Name, dsvc); err != nil {
			return err
		}
	}
	mcm, mcmErr := controller.BuildManifestConfigMapForPlan(agent)
	if mcmErr != nil {
		return fmt.Errorf("build manifest ConfigMap: %w", mcmErr)
	}
	if mcm != nil {
		if err := emit("ConfigMap", mcm.Name, mcm); err != nil {
			return err
		}
	}
	return nil
}
