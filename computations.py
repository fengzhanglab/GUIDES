import doench_score # calc_score(seq)
from Queue import PriorityQueue
import re
import pandas as pd
import numpy as np
import pickle
import seq_generator
from settings import APP_STATIC
import os
import itertools

class GuideRNA():
  """Holder of gRNA information"""
  newid = itertools.count().next

  def __init__(self, selected, start, seq, PAM, score, exon_ranking, ensembl_gene, gene_name):
    self.start = start
    self.seq = seq
    self.PAM = PAM
    self.score = score
    self.exon_ranking = exon_ranking
    self.ensembl_gene = ensembl_gene
    self.gene_name = gene_name
    self.selected = selected

    self.uid = "customLibrary_guide" + str(GuideRNA.newid()).zfill(4)

  def serialize_for_display(self):
    """Serialize for the way we are returning json"""
    return {
      "score": self.score,
      "start": self.start,
      "seq": self.seq,
      "PAM": self.PAM,
      "selected": self.selected,
      "uid": self.uid
    }

  # Comparison technique - describes ranking DISCUSS (include exon_ranking?)
  # We assume higher deonch score is better -- otherwise, we should swap self and other scores.
  def __cmp__(self, other):
    return cmp(self.score, other.score)

# Species info is not yet incorporated
class Ranker():
  """Finds and ranks gRNAs from a given gene and species"""
  def __init__(self, genome, species, tissues, gtex_enabled, tissues_enabled, PAM='NGG', prime5=True, protospacer_len=20, scoring_alg={"Doench"}):
    self.genome = genome
    self.species = species
    self.tissues = tissues
    self.gtex_enabled = gtex_enabled
    self.tissues_enabled = tissues_enabled
    self.PAM_len = len(PAM)
    self.prime5 = prime5
    self.protospacer_len = protospacer_len
    self.scoring_alg = scoring_alg

    self.genes = []
    self.gRNAs = []
    self.countSelectedGuides = 0
    self.expression_values = {}

    # Prepare PAM
    PAM = PAM.upper()
    PAM = PAM.replace('N', '[ATCG]')
    self.PAM = "(?=" + PAM + ")"

    # Doench score
    self.use_Doench = "Doench" in scoring_alg

    # Load pre-processed GTEx data
    self.df_normalized = pickle.load(open(os.path.join(APP_STATIC, 'data/pre_processed', 'pd_by_tissue_normalized.p'), "rb"))

  # ensembl_gene - ensembl encocoding for the gene (grCH37)
  # gene_name - user-friendly name for the gene
  def rank(self, ensembl_gene, gene_name, quantity):
    # Get the revcompl of a sequence print revcompl("AGTCAGCAT")
    revcompl = lambda x: ''.join([{'A':'T','C':'G','G':'C','T':'A'}[B] for B in x][::-1])

    df_gene = self.df_normalized[self.df_normalized.Id.str.contains(ensembl_gene)]
    self.genes.append((ensembl_gene, gene_name))

    # Sort by exon number, removing first and last exon
    # Recall: Id are entered as ENSG000xxxxx.x_EXONNUM, e.g. ENSG00000000971.11_21
    df_gene['exon_num'] = df_gene['Id'].apply(lambda x: int(x.split('_')[1]))
    # Get median expression for selected tissues
    df_gene['median'] = df_gene[self.tissues].median(axis=1)
    df_gene['overall'] = df_gene.median(axis=1)
    expression_values = {}
    for index, row in df_gene.iterrows():
      expression_value = {
        'median': row['median'],
        'overall': row['overall'],
        'brain': row['Brain'],
        'heart': row['Heart'],
        'kidney': row['Kidney'],
        'liver': row['Liver'],
        'skin': row['Skin']
      }
      expression_values[int(row['exon_num'])] = expression_value
    self.expression_values[gene_name] = expression_values

    if len(df_gene) > 4:
      df_gene = df_gene.sort(['exon_num'], ascending=True).iloc[1:-1]

    df_results =  df_gene[['Id', 'median', 'overall','exon_num']]
    df_results = df_results.sort(['median'], ascending=False)

    # For this gene, analyze top 4 exons, at most
    q = PriorityQueue()

    # Focus on top 4 exons if gtex_enabled...otherwise use all.
    exons_to_analyze = len(df_results)
    if self.gtex_enabled == True:
      exons_to_analyze = min(4, len(df_results))

    total_exons = len(df_results)

    # for i in range(min(4, len(df_results))):
    i = 0
    while (i < total_exons) and (i < exons_to_analyze or q.qsize() < quantity):
      # Generate pseudogenome
      gtex_gene_exon = str(df_results.iloc[i]['Id'])
      gtex_exon_num = int(df_results.iloc[i]['exon_num'])
      seq = str(self.genome.sequence_gtex_gene(gtex_gene_exon))

      # Search for the NGG PAM, beginning after the 21st base pair. DISCUSS (introns needed?)
      # Makes no sense to run if sequence is too short
      if self.use_Doench and len(seq) < 30:
        i += 1
        continue

      def process_guide(m, selected, max_queue_size, seq): # m for match
        PAM_start = m.start()
        score = 0
        if self.use_Doench:
          # Doench score requires the 4 before and 6 after 20-mer (gives 30-mer)
          mer30 = seq[PAM_start-self.protospacer_len-4:PAM_start+self.PAM_len+3]
          if len(mer30) == 30:
            score = doench_score.calc_score(mer30)

        protospacer = ""
        PAM = ""
        if self.prime5:
          protospacer = seq[PAM_start-self.protospacer_len:PAM_start]
          PAM = seq[PAM_start:PAM_start+self.PAM_len]
        else:
          protospacer = seq[PAM_start+self.PAM_len:PAM_start+self.PAM_len+self.protospacer_len]
          PAM = seq[PAM_start:PAM_start+self.PAM_len]
        potential_gRNA = GuideRNA(selected, PAM_start-self.protospacer_len, protospacer, PAM, score, gtex_exon_num, ensembl_gene, gene_name)

        # If there's enough room, add it, no question.
        if q.qsize() < max_queue_size:
          q.put(potential_gRNA)
        # Otherwise, take higher score
        else:
          lowest_gRNA = q.get()
          if potential_gRNA.score > lowest_gRNA.score:
            q.put(potential_gRNA)
          else:
            q.put(lowest_gRNA)

      for m in re.finditer(self.PAM, seq):
        if self.prime5 and (m.start() < self.protospacer_len or m.start() + self.PAM_len > len(seq)):
          continue
        elif not self.prime5 and (m.start() + self.PAM_len + self.protospacer_len > len(seq)):
          continue
        process_guide(m, True, quantity, seq)
      seq_rc = revcompl(seq)
      for m in re.finditer(self.PAM, seq_rc):
        if self.prime5 and (m.start() < self.protospacer_len or m.start() + self.PAM_len > len(seq)):
          continue
        elif not self.prime5 and (m.start() + self.PAM_len + self.protospacer_len > len(seq)):
          continue
        process_guide(m, True, quantity, seq_rc)
      i += 1

    # Pop gRNAs into our 'permanent' storage 
    while not q.empty():
      gRNA = q.get()
      if gRNA.selected:
        self.countSelectedGuides += 1
      self.gRNAs.append(gRNA)

    # add at least k=10 guides from other exons, mark unselected
    min_per_exon = 10
    while i < total_exons:
      # make a new priority queue for this analysis
      q = PriorityQueue()

      # Generate pseudogenome
      gtex_gene_exon = str(df_results.iloc[i]['Id'])
      gtex_exon_num = int(df_results.iloc[i]['exon_num'])
      seq = str(self.genome.sequence_gtex_gene(gtex_gene_exon))

      # Search for the NGG PAM, beginning after the 21st base pair. DISCUSS (introns needed?)
      # Makes no sense to run if sequence is too short
      if self.use_Doench and len(seq) < 30:
        i += 1
        continue

      def process_guide(m, selected, max_queue_size, seq): # m for match
        PAM_start = m.start()
        score = 0
        if self.use_Doench:
          # Doench score requires the 4 before and 6 after 20-mer (gives 30-mer)
          mer30 = seq[PAM_start-self.protospacer_len-4:PAM_start+self.PAM_len+3]
          if len(mer30) == 30:
            score = doench_score.calc_score(mer30)

        protospacer = ""
        PAM = ""
        if self.prime5:
          protospacer = seq[PAM_start-self.protospacer_len:PAM_start]
          PAM = seq[PAM_start:PAM_start+self.PAM_len]
        else:
          protospacer = seq[PAM_start+self.PAM_len:PAM_start+self.PAM_len+self.protospacer_len]
          PAM = seq[PAM_start:PAM_start+self.PAM_len]
        potential_gRNA = GuideRNA(selected, PAM_start-self.protospacer_len, protospacer, PAM, score, gtex_exon_num, ensembl_gene, gene_name)

        # If there's enough room, add it, no question.
        if q.qsize() < max_queue_size:
          q.put(potential_gRNA)
        # Otherwise, take higher score
        else:
          lowest_gRNA = q.get()
          if potential_gRNA.score > lowest_gRNA.score:
            q.put(potential_gRNA)
          else:
            q.put(lowest_gRNA)

      for m in re.finditer(self.PAM, seq):
        if self.prime5 and (m.start() < self.protospacer_len or m.start() + self.PAM_len > len(seq)):
          continue
        elif not self.prime5 and (m.start() + self.PAM_len + self.protospacer_len > len(seq)):
          continue
        process_guide(m, False, min_per_exon, seq)
      seq_rc = revcompl(seq)
      for m in re.finditer(self.PAM, seq_rc):
        if self.prime5 and (m.start() < self.protospacer_len or m.start() + self.PAM_len > len(seq)):
          continue
        elif not self.prime5 and (m.start() + self.PAM_len + self.protospacer_len > len(seq)):
          continue
        process_guide(m, False, min_per_exon, seq_rc)

      # Deposit the guides for this exon into our permanent source
      while not q.empty():
        gRNA = q.get()
        if gRNA.selected:
          self.countSelectedGuides += 1
        self.gRNAs.append(gRNA)

      i += 1

  def get_guides_by_exon(self):
    # First, setup data as an associative array, to make guide insertion easier
    # Later, we'll transform to ordered array.
    gene_to_exon = {}
    for (ensembl_gene, gene_name) in self.genes:
      gene_info = self.genome.gene_info(ensembl_gene)

      gene_to_exon[gene_name] = {
        "name": gene_name,
        "ensembl_gene": ensembl_gene,
        "length": gene_info['txEnd'] - gene_info['txStart'],
        "start": gene_info['txStart'],
        "end": gene_info['txEnd'],
        "exons": [] # Gets filled in below
      }

      # Prepare each exon and add to gene_to_exon[gene_name].exons
      for i in range(gene_info['exonCount']):
        expression_value = self.expression_values[gene_name][i]
        expression_value_returned = dict(expression_value)
        if not self.tissues_enabled:
          del expression_value_returned["median"]
        exon = {
          "start": gene_info['exonStarts'][i] - gene_info['txStart'],
          "end": gene_info['exonEnds'][i] - gene_info['txStart'],
          "gRNAs": [], # Gets filled in below
          "expression": expression_value_returned
        }
        gene_to_exon[gene_name]['exons'].append(exon)

    # Iterate through guides and add to appropriate exon
    for guide in self.gRNAs:
      gene_to_exon[guide.gene_name]['exons'][guide.exon_ranking]['gRNAs'].append(guide.serialize_for_display())

    # Convert associative array to ordered array
    guides_by_exon = []
    for _, value in gene_to_exon.iteritems():
      guides_by_exon.append(value)

    return guides_by_exon

  def get_count_selected_guides(self):
    return self.countSelectedGuides

def run():
  suma = 0
  for i in range(5):
    suma += i
  return suma

# for testing/profiling issues
if __name__ == '__main__':
    # Request arguments
  genes = None
  species  = None
  quantity = None
  tissues  = None

  # Validations
  if genes == None:
    genes = [('ENSG00000115977.14', "AAK1"), ('ENSG00000142168.10', "SOD1")]
  if species == None:
    species = "human"
  if quantity == None:
    quantity = 60
  if tissues == None:
    tissues = ["Muscle", "Heart", "Brain"]

  quantity_per_gene = quantity/len(genes)

  # Setup ranker
  genome = {
    "human" : seq_generator.FastGenome()
  }
  ranker = Ranker(genome["human"], species, tissues)

  # Iterate over genes, finding guides for each
  for (ensembl_gene, gene_name) in genes:
    ranker.rank(ensembl_gene, gene_name, quantity_per_gene)

  guides_by_exon = ranker.get_guides_by_exon()
  run()
