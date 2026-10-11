"""Offline negative controls for the frozen minimum-required-suite oracle."""
import argparse
import json
from pathlib import Path
from test_recommendation.catalog import CatalogError, load_catalog, require
from test_recommendation.change_pipeline import checksum, save

VERSION='negative-controls-v1'


def evaluate(case, prediction, suite_ids):
    required=set(case['required_suite_ids'])
    require(required and required <= suite_ids, 'Invalid golden required suites')
    errors=[]
    for field in ('case_id','case_version','base_revision_sha','target_commit_sha'):
        if prediction.get(field)!=case[field]:
            errors.append('mismatched_'+field)
    selected=prediction.get('selected_suite_ids')
    if not isinstance(selected,list) or not all(isinstance(s,str) for s in selected):
        errors.append('invalid_selected_suite_ids');selected=[]
    if len(selected)!=len(set(selected)):
        errors.append('duplicate_suite_ids')
    unknown=sorted(set(selected)-suite_ids)
    if unknown:
        errors.append('unknown_suite_ids')
    known=set(selected)&suite_ids
    missing=sorted(required-known)
    extras=sorted(known-required)
    valid=not errors
    return dict(case_id=case['case_id'],case_version=case['case_version'],base_revision_sha=case['base_revision_sha'],
                target_commit_sha=case['target_commit_sha'],status='invalid' if errors else 'missing_required' if missing else 'required_suites_present',
                valid=valid,validation_errors=errors,unknown_suite_ids=unknown,missing_required_suite_ids=missing,
                extra_suite_ids_for_review=extras,required_suite_recall=len(required&known)/len(required) if valid else None,
                selected_unique_known_suite_count=len(known),required_suite_count=len(required),extra_suite_count=len(extras),
                catalog_selection_fraction=len(known)/len(suite_ids),
                all_catalog_selected=known==suite_ids,availability_verified=False,tests_executed=False,
                limitation='Extras need evidence review; catalog fraction is not measured runtime, target availability or fault detection.')


def prediction(case, selected):
    return dict(**{k:case[k] for k in ('case_id','case_version','base_revision_sha','target_commit_sha')},selected_suite_ids=selected)


def controls(case, suite_ids):
    required=sorted(case['required_suite_ids'])
    other=next(s for s in sorted(suite_ids) if s not in required)
    wrong_revision=prediction(case,required)
    wrong_revision['target_commit_sha']='0'*40
    return [
        ('positive_required',prediction(case,required),'required_suites_present',False),
        ('drop_required',prediction(case,required[1:]),'missing_required',False),
        ('replace_required_with_unvetted_extra',prediction(case,required[1:]+[other]),'missing_required',True),
        ('add_unvetted_extra',prediction(case,required+[other]),'required_suites_present',True),
        ('select_entire_catalog',prediction(case,sorted(suite_ids)),'required_suites_present',True),
        ('unknown_suite',prediction(case,required+['__unknown_suite__']),'invalid',False),
        ('duplicate_suite',prediction(case,required+[required[0]]),'invalid',False),
        ('wrong_revision',wrong_revision,'invalid',False),
    ]


def run(dataset,catalog,predictions=None):
    require(dataset['catalog_key']==checksum(catalog) and dataset['corpus_id']==catalog['corpus_id'], 'Golden/catalog identity mismatch')
    require(dataset['oracle_kind']=='required_behavior_suites', 'Unsupported golden oracle')
    cases=dataset['cases']
    keys={(c['case_id'],c['case_version']) for c in cases}
    require(len(keys)==len(cases) and len(cases)==20, 'Expected 20 unique frozen golden cases')
    suite_ids={s['id'] for s in catalog['suites']}
    report=dict(stage='negative_case_evaluation',stage_version=VERSION,dataset_id=dataset['dataset_id'],
                dataset_version=dataset['dataset_version'],dataset_sha256=checksum(dataset),catalog_sha256=checksum(catalog),
                label_semantics=dataset['label_semantics'],human_approved=dataset['human_approved'],tests_executed=False,
                mode='negative_controls' if predictions is None else 'submitted_predictions',results=[])
    if predictions is None:
        for case in cases:
            for name,predicted,expected,needs_review in controls(case,suite_ids):
                actual=evaluate(case,predicted,suite_ids)
                passed=actual['status']==expected and bool(actual['extra_suite_ids_for_review'])==needs_review
                if name=='select_entire_catalog':
                    passed=passed and actual['all_catalog_selected']
                report['results'].append(dict(control=name,expected_status=expected,control_passed=passed,**actual))
        report['summary']=dict(golden_cases=len(cases),controls=len(report['results']),
                               passed=sum(r['control_passed'] for r in report['results']),
                               failed=sum(not r['control_passed'] for r in report['results']))
    else:
        require(isinstance(predictions,list) and all(isinstance(p,dict) for p in predictions), 'Predictions must be a list of objects')
        indexed={c['case_id']:c for c in cases}
        require(len({p.get('case_id') for p in predictions})==len(predictions), 'Duplicate prediction case IDs')
        require({p.get('case_id') for p in predictions}==set(indexed), 'Submit exactly one prediction for each golden case')
        report['predictions_sha256']=checksum(predictions)
        report['results']=[evaluate(indexed[p['case_id']],p,suite_ids) for p in predictions]
        valid=[r for r in report['results'] if r['valid']]
        report['summary']=dict(cases=len(cases),invalid=sum(not r['valid'] for r in report['results']),
            missing_required=sum(r['status']=='missing_required' for r in report['results']),
            cases_with_extras_for_review=sum(bool(r['extra_suite_ids_for_review']) for r in report['results']),
            macro_required_suite_recall=sum(r['required_suite_recall'] for r in valid)/len(valid) if valid else None,
            recall_denominator_valid_cases=len(valid))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,default=Path('evals/golden_dataset_v1.json'))
    parser.add_argument('--catalog',type=Path,default=Path('test_catalog.yaml'))
    parser.add_argument('--predictions',type=Path)
    parser.add_argument('--output',type=Path,default=Path('artifacts/evaluation/negative_cases/report.json'))
    args=parser.parse_args()
    try:
        dataset=json.loads(args.dataset.read_text())
        predictions=json.loads(args.predictions.read_text()) if args.predictions else None
        result=run(dataset,load_catalog(args.catalog),predictions)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        save(args.output,result)
        print(json.dumps(result['summary'],sort_keys=True))
        print('Report:',args.output)
        if result['summary'].get('failed',0) or result['summary'].get('invalid',0) or result['summary'].get('missing_required',0):
            parser.exit(1,'Evaluation detected failures.\n')
    except (CatalogError,OSError,ValueError,KeyError,TypeError) as error:
        parser.exit(1,f'Negative evaluation failed: {error}\n')

if __name__=='__main__':
    main()
